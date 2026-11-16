const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/app.js', 'utf8');

function harness(hash = '#inicio') {
  const nodes = new Map(), calls = [];
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, { value: id === 'businessId' ? 'one' : '',
      textContent: '', innerHTML: '', hidden: true, disabled: false, dataset: {},
      classList: { toggle() {}, add() {}, remove() {} }, addEventListener() {},
      querySelector: () => ({ disabled: false }), setAttribute() {}, removeAttribute() {},
    });
    return nodes.get(id);
  };
  let resolve, reject;
  const home = new Promise((yes, no) => { resolve = yes; reject = no; });
  const context = vm.createContext({
    document: { getElementById: node, querySelectorAll: () => [], querySelector: () => node('shell') },
    location: { hash }, window: { addEventListener() {}, scrollTo() {},
      STOCK_AI_CONFIG: { authEnabled: true }, StockAuth: { onReady() {} } },
    URLSearchParams, console, Date, calls,
    query: async (url) => { calls.push(url); return home; },
    lazy: async (name) => { calls.push(name); },
  });
  vm.runInContext(source, context);
  vm.runInContext(`requestJson = query;
    renderOperationsDashboard = () => {};
    renderSalesStatus = () => calls.push('renderSalesStatus');
    loadDocuments = () => lazy('documents');
    loadAnalysisMethods = () => lazy('capabilities');
    loadCatalogStatus = () => lazy('catalog');
    loadSalesStatus = () => lazy('sales');
    recoverAnalysisJobs = () => lazy('recover');`, context);
  return { context, calls, resolve: () => resolve({ sales_status: {}, recent_supplier_offers: [] }),
    reject, run: (code) => vm.runInContext(code, context),
    settle: () => new Promise((done) => setImmediate(done)) };
}

test('initial home fetches only its compact data and retains job recovery after load', async () => {
  const h = harness();
  h.run('startAuthenticatedApp()');
  assert.equal(h.calls.length, 1);
  assert.match(h.calls[0], /^\/operations\/home\?/);
  assert.equal(h.run('loadOperationsDashboard() === homeLoad'), true);
  h.resolve(); await h.settle();
  assert.deepEqual(h.calls.slice(1), ['renderSalesStatus', 'recover']);
  await h.run('loadViewData("comparaciones")');
  await h.run('loadViewData("comparaciones")');
  assert.equal(h.calls.filter((call) => call === 'documents').length, 1);
  assert.equal(h.calls.filter((call) => call === 'capabilities').length, 1);
});

test('Datos waits for home and reuses its sales status instead of duplicating the request', async () => {
  const h = harness('#datos');
  h.run('startAuthenticatedApp()');
  assert.equal(h.calls.length, 1);
  h.resolve(); await h.settle();
  assert.equal(h.calls.filter((call) => call === 'catalog').length, 1);
  assert.equal(h.calls.includes('sales'), false);
  assert.equal(h.calls.includes('documents'), false);
});

test('direct Comparaciones links load lists and methods without loading Datos', async () => {
  const h = harness('#comparaciones');
  h.run('startAuthenticatedApp()');
  await h.settle();
  assert.equal(h.calls.includes('documents'), true);
  assert.equal(h.calls.includes('capabilities'), true);
  assert.equal(h.calls.includes('catalog'), false);
  h.resolve(); await h.settle();
});

test('failed home does not prevent job recovery or Datos fallback', async () => {
  const h = harness('#datos');
  h.run('startAuthenticatedApp()');
  h.reject(new Error('Network failed')); await h.settle();
  assert.equal(h.calls.includes('recover'), true);
  assert.equal(h.calls.includes('sales'), true);
  assert.equal(h.calls.includes('catalog'), true);
});

test('failed lazy section can retry and concurrent entries do not duplicate its request', async () => {
  const h = harness();
  let fail = true;
  h.context.lazy = async (name) => { h.calls.push(name); if (fail) throw new Error('Network failed'); };
  await Promise.all([h.run('loadViewData("comparaciones")'), h.run('loadViewData("comparaciones")')]);
  assert.equal(h.calls.filter((call) => call === 'documents').length, 1);
  fail = false;
  await h.run('loadViewData("comparaciones")');
  assert.equal(h.calls.filter((call) => call === 'documents').length, 2);
});
