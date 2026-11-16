const { test } = require('node:test');
const assert = require('node:assert/strict');
const createTransport = require('../../app/static/transport.js');
const createJobs = require('../../app/static/jobs.js');

const response = (data, status = 200) => new Response(JSON.stringify(data), { status });
function memory(initial = []) {
  const map = new Map([['stock-ai.jobs.v1', JSON.stringify(initial)]]);
  return { getItem: (key) => map.get(key), setItem: (key, value) => map.set(key, value) };
}
const job = (status = 'succeeded', id = 'job-one') => ({ job_id: id, status,
  status_url: `/api/procurement/supplier-offer-analysis-jobs/${id}?business_id=one`,
  result_url: `/api/procurement/supplier-offer-analysis-jobs/${id}/result?business_id=one` });
function jobs(fetch, extra = {}) {
  return createJobs({ transport: { fetch }, storage: memory(), scope: () => 'user:one',
    uuid: () => 'key-one', sleep: async () => {}, ...extra });
}

test('same-origin bearer transport preserves multipart and binary downloads', async () => {
  const calls = [];
  const transport = createTransport({ config: { authEnabled: true, apiBaseUrl: '/api' },
    getToken: async () => 'session-token', fetch: async (...args) => { calls.push(args); return new Response('PDF'); } });
  const form = new FormData(); form.append('business_id', 'one');
  await transport.fetch('/procurement/upload', { method: 'POST', body: form, headers: { 'X-Stock-AI-Origin': 'forged' } });
  const pdf = await transport.fetch('/procurement/plan.pdf?business_id=one');
  assert.equal(await (await pdf.blob()).text(), 'PDF');
  assert.equal(calls[0][0], '/api/procurement/upload');
  assert.equal(calls[0][1].body, form);
  for (const [, options] of calls) {
    assert.equal(options.headers.get('Authorization'), 'Bearer session-token');
    assert.equal(options.headers.has('X-Stock-AI-Origin'), false);
    assert.equal(options.headers.has('Content-Type'), false);
    assert.equal(options.redirect, 'error');
    assert.equal(options.cache, 'no-store');
  }
});

test('401 and uncertain POST are never automatically repeated', async () => {
  let requests = 0, unauthorized = 0;
  const transport = createTransport({ config: { authEnabled: true, apiBaseUrl: '/api' }, getToken: async () => 'token',
    fetch: async () => { requests++; return response({}, 401); }, unauthorized: () => unauthorized++ });
  assert.equal((await transport.fetch('/assistant/message', { method: 'POST' })).status, 401);
  assert.equal(requests, 1); assert.equal(unauthorized, 1);
});

test('transport rejects external paths and traversal before fetching', async () => {
  const transport = createTransport({ config: { authEnabled: false, apiBaseUrl: '/api' }, fetch: () => assert.fail('unexpected request') });
  for (const path of ['https://evil.example/', '//evil.example/', '/procurement/../health', '/procurement/%2e%2e/health', '/health', '/procurement/a#x']) {
    await assert.rejects(transport.fetch(path), /inválida/);
  }
});

test('session change rejects bodies received after cancellation', async () => {
  let finish;
  const transport = createTransport({ config: { authEnabled: false, apiBaseUrl: '/api' }, fetch: async () => ({
    status: 200, ok: true, json: () => new Promise((resolve) => { finish = resolve; }),
  }) });
  const result = await transport.fetch('/operations/dashboard');
  const reading = result.json();
  transport.cancelAll(); finish({ private: 'old tenant' });
  await assert.rejects(reading, { name: 'AbortError' });
});

test('lost 202 recovers by the same idempotency key with a single upload', async () => {
  const calls = [], storage = memory();
  const client = jobs(async (path, options) => {
    calls.push([path, options]);
    if (options?.method === 'POST') throw new TypeError('connection lost');
    if (path.includes('idempotency_key=')) return response({ jobs: [job()] });
    return response({ import_result: { document: { id: 'document-one' } } });
  }, { storage });
  const result = await client.analyze(new FormData(), 'one');
  assert.equal(result.import_result.document.id, 'document-one');
  assert.equal(calls.filter(([, options]) => options?.method === 'POST').length, 1);
  assert.equal(calls[0][1].headers['Idempotency-Key'], 'key-one');
  assert.match(calls[1][0], /idempotency_key=key-one/);
  assert.deepEqual(JSON.parse(storage.getItem('stock-ai.jobs.v1')), []);
});

test('transient polling failure keeps the accepted job and does not repeat upload', async () => {
  let posts = 0, polls = 0;
  const client = jobs(async (path, options) => {
    if (options?.method === 'POST') { posts++; return response(job('queued')); }
    if (path.includes('/result?')) return response({ result: true });
    if (++polls === 1) throw new TypeError('offline');
    return response(job());
  });
  assert.deepEqual(await client.analyze(new FormData(), 'one'), { result: true });
  assert.equal(posts, 1); assert.equal(polls, 2);
});

test('logout stops polling but retains only a scoped durable reference', async () => {
  const storage = memory(); let client;
  client = jobs(async () => response(job('queued')), { storage, sleep: async () => client.stop() });
  await assert.rejects(client.analyze(new FormData(), 'one'), { name: 'AbortError' });
  assert.deepEqual(JSON.parse(storage.getItem('stock-ai.jobs.v1')), [{ key: 'key-one', businessId: 'one', scope: 'user:one', jobId: 'job-one' }]);
});

test('recovery respects scope, pagination and expired references without uploading', async () => {
  const storage = memory([
    { key: 'old', businessId: 'one', scope: 'user:one', jobId: 'expired' },
    { key: 'private', businessId: 'other', scope: 'other:other', jobId: 'private' },
  ]);
  const results = [], calls = [];
  const client = jobs(async (path, options) => {
    assert.notEqual(options?.method, 'POST'); calls.push(path);
    if (path.includes('/expired?')) return response({}, 404);
    if (path.includes('/result?')) return response({ result: true });
    if (path.includes('status=queued') && path.includes('offset=0')) return response({ jobs: [job()], next_offset: 100 });
    return response({ jobs: [], next_offset: null });
  }, { storage });
  await client.recover('one', (data) => results.push(data));
  assert.deepEqual(results, [{ result: true }]);
  assert.ok(calls.some((path) => path.includes('offset=100')));
  assert.ok(calls.every((path) => !path.includes('private')));
  assert.deepEqual(JSON.parse(storage.getItem('stock-ai.jobs.v1')), [{ key: 'private', businessId: 'other', scope: 'other:other', jobId: 'private' }]);
});

test('failure in one recovered job does not strand the next result', async () => {
  const results = [];
  const client = jobs(async (path) => path.includes('/result?') ? response({ result: true }) :
    response({ jobs: path.includes('status=queued') ? [job('failed', 'bad'), job()] : [], next_offset: null }));
  await assert.rejects(client.recover('one', (data) => results.push(data)), /no pudo completarse/);
  assert.deepEqual(results, [{ result: true }]);
});

test('explicit continuation binds the source document and posts exactly once', async () => {
  const calls = [];
  const client = jobs(async (path, options) => {
    calls.push([path, options]);
    if (options?.method === 'POST') return response(job());
    return response({ continued: true });
  });
  assert.deepEqual(await client.continueAnalysis('job-one', 'one', 'document-one'), { continued: true });
  assert.equal(calls.filter(([, options]) => options?.method === 'POST').length, 1);
  assert.match(calls[0][0], /source_document_id=document-one/);
});
