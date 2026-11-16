const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/app.js', 'utf8');
const functions = source.slice(source.indexOf('let rankingProducts = []'), source.indexOf('$("catalogForm").addEventListener'));

function harness() {
  const nodes = new Map(), calls = [];
  const state = { tenant: 'one', scope: 'subject:one', post: null, preferencesError: false };
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, { value: '', innerHTML: '', textContent: '' });
    return nodes.get(id);
  };
  node('rankingProductsQuery').value = 'bolsa';
  const context = vm.createContext({
    $: node, businessId: () => state.tenant, window: { StockAuth: { scope: () => state.scope } },
    URLSearchParams, encodeURIComponent,
    escapeHtml: (value) => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
    requestJson: async (url, options) => {
      calls.push({ url, options });
      if (options?.method === 'POST') {
        if (state.post) return state.post();
        return { business_id: 'one', product_id: 'bag', ...JSON.parse(options.body) };
      }
      if (url.includes('/analysis-preferences?')) {
        if (state.preferencesError) throw new Error('unavailable');
        return { business_id: 'one', excluded_product_ids: ['bag'] };
      }
      return [{ id: 'bag', name: '<bolsa>', sku: 'SKU-1', barcode: '779' }];
    },
  });
  vm.runInContext(functions, context);
  const run = (code) => vm.runInContext(code, context);
  const input = { dataset: { rankingProductId: 'bag' }, checked: false, disabled: false };
  context.input = input;
  return { node, calls, state, input, run };
}

test('product preference is loaded by commerce and reversible using a concrete product ID', async () => {
  const h = harness();
  await h.run('searchRankingProducts()');
  assert.match(h.node('rankingProductsBody').innerHTML, /&lt;bolsa>/);
  assert.match(h.node('rankingProductsBody').innerHTML, /checked/);
  await h.run('saveRankingPreference(input)');
  const post = h.calls.find(({ options }) => options?.method === 'POST');
  assert.match(post.url, /\/bag\/analysis-preference$/);
  assert.deepEqual(JSON.parse(post.options.body), { business_id: 'one', exclude_from_sales_rankings: false });
  assert.equal(h.run('rankingExcludedIds.has("bag")'), false);
  h.input.checked = true;
  await h.run('saveRankingPreference(input)');
  assert.equal(h.run('rankingExcludedIds.has("bag")'), true);
  assert.match(h.node('rankingProductsStatus').textContent, /ventas siguen en los totales/);
});

test('initial preference failure never enables editing or assumes every product is relevant', async () => {
  const h = harness();
  h.state.preferencesError = true;
  await h.run('searchRankingProducts()');
  assert.equal(h.run('rankingExcludedIds'), null);
  assert.doesNotMatch(h.node('rankingProductsBody').innerHTML, /checkbox/);
  await h.run('saveRankingPreference(input)');
  assert.equal(h.calls.filter(({ options }) => options?.method === 'POST').length, 0);
});

test('failed save restores prior state and never presents success', async () => {
  const h = harness();
  await h.run('searchRankingProducts()');
  h.state.post = async () => { throw new Error('lost response'); };
  await h.run('saveRankingPreference(input)');
  assert.equal(h.input.checked, true);
  assert.equal(h.run('rankingExcludedIds.has("bag")'), true);
  assert.equal(h.input.disabled, false);
  assert.match(h.node('rankingProductsStatus').textContent, /No pudimos confirmar/);
});

test('late save after tenant change or logout cannot update the current view', async () => {
  for (const change of ['tenant', 'logout']) {
    const h = harness();
    await h.run('searchRankingProducts()');
    let complete;
    h.state.post = () => new Promise((resolve) => { complete = resolve; });
    const pending = h.run('saveRankingPreference(input)');
    assert.equal(h.input.disabled, true);
    if (change === 'tenant') h.state.tenant = 'two'; else h.state.scope = 'none';
    h.node('rankingProductsStatus').textContent = 'new view';
    complete({ business_id: 'one', product_id: 'bag', exclude_from_sales_rankings: false });
    await pending;
    assert.equal(h.node('rankingProductsStatus').textContent, 'new view');
    assert.equal(h.run('rankingExcludedIds.has("bag")'), true);
  }
});
