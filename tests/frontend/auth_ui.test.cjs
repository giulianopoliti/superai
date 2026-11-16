const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('frontend/auth-entry.mjs', 'utf8').replace(/^import .*;\s*/, '');

function harness(session = null, options = {}) {
  const nodes = new Map(), values = new Map(), listeners = {}, calls = [], windowListeners = {};
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, { hidden: false, value: '', textContent: '',
      addEventListener(event, callback) { listeners[`${id}:${event}`] = callback; }, replaceChildren() {} });
    return nodes.get(id);
  };
  const state = { session };
  let cancelled = 0, reloads = 0, ready = 0, refreshes = 0;
  const auth = {
    getSession: async () => ({ data: { session: state.session } }),
    refreshSession: async () => { refreshes++; await Promise.resolve(); state.session = { ...state.session, expires_at: Date.now() / 1000 + 3600, access_token: 'fresh' }; return { data: { session: state.session } }; },
    signInWithPassword: async (credentials) => { calls.push(credentials); return { error: new Error('bad password') }; },
    signOut: async () => {}, onAuthStateChange() {},
  };
  const window = { STOCK_AI_CONFIG: { authEnabled: true, supabaseUrl: 'https://example.supabase.co', supabasePublishableKey: 'sb_publishable_test' },
    sessionStorage: { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) },
    addEventListener(event, callback) { windowListeners[event] = callback; },
    StockTransport: { cancelAll() { cancelled++; }, fetch: async () => {
      calls.push('/auth/me');
      if (options.fetch) return options.fetch();
      return { ok: true, json: async () => ({ businesses: [{ business_id: 'one', business_name: 'Commerce', display_name: 'Operator' }] }) };
    } }, StockJobs: { stop() {} }, location: { reload() { reloads++; } },
  };
  const context = vm.createContext({ window, document: { getElementById: node, querySelector: () => node('shell'), createElement: () => ({}) },
    createClient: () => ({ auth }), setTimeout, Date, console });
  vm.runInContext(source, context);
  window.StockAuth.onReady(() => ready++);
  return { window, state, node, calls, listeners, windowListeners, settle: () => new Promise((resolve) => setImmediate(resolve)),
    counts: () => ({ cancelled, reloads, ready, refreshes }) };
}

test('no session shows login and never requests business data', async () => {
  const h = harness(); await h.settle();
  assert.equal(h.node('shell').hidden, true);
  assert.equal(h.node('loginPanel').hidden, false);
  assert.deepEqual(h.calls, []);
  assert.equal(h.counts().ready, 0);
});

test('restored session requires auth/me and only allows assigned businesses', async () => {
  const h = harness({ user: { id: 'subject' }, access_token: 'token', expires_at: Date.now() / 1000 + 3600 });
  await h.settle();
  assert.deepEqual(h.calls, ['/auth/me']);
  assert.equal(h.node('shell').hidden, false);
  assert.equal(h.node('businessId').value, 'one');
  assert.equal(h.node('businessId').readOnly, true);
  assert.equal(h.counts().ready, 1);
  assert.equal(h.window.StockAuth.selectBusiness('other'), false);
  assert.equal(h.window.StockAuth.scope(), 'subject:one');
});

test('failed login clears password and presents generic error', async () => {
  const h = harness(); await h.settle();
  h.node('loginEmail').value = ' operator@example.com ';
  h.node('loginPassword').value = 'test-only-password';
  await h.listeners['loginForm:submit']({ preventDefault() {} });
  assert.equal(h.node('loginPassword').value, '');
  assert.match(h.node('loginStatus').textContent, /Revisá el correo/);
  assert.equal(h.node('shell').hidden, true);
});

test('concurrent requests share one refresh and logout clears session DOM', async () => {
  const h = harness({ user: { id: 'subject' }, access_token: 'old', expires_at: 1 });
  await h.settle();
  assert.deepEqual(await Promise.all([h.window.StockAuth.accessToken(), h.window.StockAuth.accessToken()]), ['fresh', 'fresh']);
  assert.equal(h.counts().refreshes, 1);
  h.listeners['logoutButton:click'](); await h.settle();
  assert.equal(h.node('shell').hidden, true);
  assert.equal(h.counts().cancelled, 1);
  assert.equal(h.counts().reloads, 1);
});

test('reload keeps private content hidden until the restored session is authorized', async () => {
  let resolveProfile;
  const pending = new Promise((resolve) => { resolveProfile = resolve; });
  const h = harness({ user: { id: 'subject' }, access_token: 'token', expires_at: Date.now() / 1000 + 3600 }, { fetch: () => pending });
  await h.settle();
  assert.equal(h.node('shell').hidden, true);
  assert.equal(h.counts().ready, 0);
  resolveProfile({ ok: false, status: 403 });
  await h.settle();
  assert.equal(h.node('shell').hidden, true);
  assert.equal(h.node('loginPanel').hidden, false);
  assert.equal(h.counts().ready, 0);
});

test('logout prevents an earlier authorization response from reopening the shell', async () => {
  let resolveProfile;
  const pending = new Promise((resolve) => { resolveProfile = resolve; });
  const h = harness({ user: { id: 'subject' }, access_token: 'token', expires_at: Date.now() / 1000 + 3600 }, { fetch: () => pending });
  await h.settle();
  h.listeners['logoutButton:click']();
  await h.settle();
  resolveProfile({ ok: true, json: async () => ({ businesses: [{ business_id: 'one' }] }) });
  await h.settle();
  assert.equal(h.node('shell').hidden, true);
  assert.equal(h.counts().ready, 0);
});

test('back-forward cache hides private content and reloads before restoring access', async () => {
  const h = harness({ user: { id: 'subject' }, access_token: 'token', expires_at: Date.now() / 1000 + 3600 });
  await h.settle();
  assert.equal(h.node('shell').hidden, false);
  h.windowListeners.pagehide({ persisted: true });
  assert.equal(h.node('shell').hidden, true);
  assert.equal(h.counts().cancelled, 1);
  h.windowListeners.pageshow({ persisted: true });
  assert.equal(h.node('shell').hidden, true);
  assert.equal(h.counts().reloads, 1);
  h.windowListeners.pageshow({ persisted: false });
  assert.equal(h.counts().reloads, 1);
});
