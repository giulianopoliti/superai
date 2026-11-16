const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { File } = require('node:buffer');
const source = fs.readFileSync('app/static/app.js', 'utf8');
const html = fs.readFileSync('app/static/index.html', 'utf8');

test('coverage distinguishes failed and never sent pages from pending offers', () => {
  const h = harness();
  h.context.analysis = { ...result().analysis, page_count: 24, read_page_count: 4,
    failed_page_numbers: [5, 6, 7, 8], unattempted_page_numbers: Array.from({length:16}, (_, i) => i + 9),
    pending_block_count: 6, interpreted_offer_count: 49, unlinked_offer_count: 49,
    identity_review_offer_count: 49, price_review_offer_count: 49 };
  const text = h.run('formatDocumentAnalysis(analysis)');
  assert.match(text, /4\/24 páginas con resultado/);
  assert.match(text, /16 páginas sin enviar/);
  assert.match(text, /4 páginas intentadas sin resultado/);
  assert.match(text, /6 bloques pendientes/);
  assert.match(text, /49 productos y 49 precios/);
  assert.doesNotMatch(h.run('formatDocumentAnalysis({method:"openai_structured"})'), /páginas con resultado/);
});

test('confirmed product with pending price basis is shown as separate decisions', () => {
  const h = harness();
  h.context.offer = { currency: '$', metadata: { analysis_v2: { interpretation: {
    identity_status: 'confirmed', price_status: 'pending', price_type: 'unknown',
  } } } };
  const text = h.run('readerAgreementHtml(offer)');
  assert.match(text, /Producto identificado/);
  assert.match(text, /Base del precio para revisar/);
  assert.match(text, /Moneda por confirmar/);
  assert.doesNotMatch(text, /Gemini|Producto para revisar/);
});

function harness() {
  const nodes = new Map();
  const make = (id) => ({ id, value: '', files: [], disabled: false, hidden: false,
    textContent: '', innerHTML: '', className: '', children: [], options: [], dataset: {},
    attributes: new Map(),
    setAttribute(name, value) { this.attributes.set(name, value); },
    removeAttribute(name) { this.attributes.delete(name); },
    appendChild(node) { this.children.push(node); },
    querySelector() { return node(`${id}-submit`); },
    get selectedOptions() { return this.options.filter((option) => option.value === this.value); },
  });
  const node = (id) => { if (!nodes.has(id)) nodes.set(id, make(id)); return nodes.get(id); };
  for (const [, id] of html.matchAll(/id="([^"]+)"/g)) node(id);
  node('decisionConfidence').value = '85';
  node('businessId').value = 'tenant-one';
  const controls = ['documentForm-submit', 'dualDocumentForm-submit', 'supplierNameA', 'supplierNameB',
    'documentFileA', 'documentFileB', 'supplierName', 'documentFile',
    'decisionConfidence'].map(node);
  const document = { getElementById: node, querySelectorAll: () => controls, createElement: () => make('link') };
  const context = vm.createContext({ document, FormData, URLSearchParams, console, fetch: async () => {
    throw new Error('Unexpected network request');
  } });
  vm.runInContext(source.slice(0, source.indexOf('$("catalogForm").addEventListener')), context);
  vm.runInContext(source.slice(source.indexOf('async function submitAnalysis'), source.indexOf('$("documentForm").addEventListener')), context);
  vm.runInContext('loadDocuments = async () => {};', context);
  const run = (code) => vm.runInContext(code, context);
  run('analysisCapabilities = { businessId: "tenant-one", method: "openai_structured" };');
  for (const suffix of ['', 'A', 'B']) {
    node(`supplierName${suffix}`).value = `Supplier${suffix}`;
    node(`documentFile${suffix}`).files = [new File(['fake PDF'], `offers${suffix}.pdf`)];
  }
  return { context, node, run };
}

function result(id = 'doc/one', method = 'openai_blocks_jev') {
  return { import_result: { document: { id, metadata: {} }, items: [] },
    comparison: { persisted_count: 0, report: { candidates: [], matched_count: 0 } },
    analysis: { method, status: 'partial', pending_block_count: 1, unlinked_offer_count: 0,
      identity_review_offer_count: 0, price_review_offer_count: 1 },
    pending_blocks: [{ page_number: 1, text: '<img src=x onerror=alert(1)>', reason: 'Falta "precio"' }] };
}

test('both methods travel in FormData independently of legacy provider', async () => {
  const h = harness();
  for (const method of ['openai_structured', 'openai_blocks_jev']) {
    h.context.method = method;
    h.run('analysisCapabilities.method = method');
    h.context.fetch = async (url, options) => {
      assert.equal(options.body.get('analysis_method'), method);
      assert.equal(options.body.get('extraction_provider'), 'auto');
      assert.equal(options.body.get('business_id'), 'tenant-one');
      assert.equal(options.body.get('file').name, 'offers.pdf');
      return { ok: true, json: async () => result() };
    };
    await h.run('analyzePdf(captureAnalysisOperation(false).uploads[0].file, "Supplier", captureAnalysisOperation(false))');
  }
});

test('validates BOTH files and names before first POST; capability required', async () => {
  const h = harness();
  let calls = 0;
  h.context.fetch = async () => { calls++; throw new Error('must not call'); };
  h.node('documentFileB').files = [new File(['bad'], 'bad.txt')];
  await h.run('submitAnalysis({preventDefault(){}}, true)');
  assert.equal(calls, 0);
  assert.match(h.node('dualResultMessage').textContent, /PDF/);
  h.run('analysisCapabilities = null');
  assert.throws(() => h.run('captureAnalysisOperation(false)'), /iniciar el análisis/);
});

test('two PDF snapshot survives DOM mutation, shared busy guard, second failure retains first', async () => {
  const h = harness();
  let resolveFirst;
  const posts = [];
  h.context.fetch = async (url, options) => {
    posts.push(options.body);
    if (posts.length === 1) return new Promise((resolve) => { resolveFirst = resolve; });
    return { ok: false, json: async () => ({ detail: 'Segundo falló' }) };
  };
  h.run('analysisCapabilities.method = "openai_blocks_jev"');
  const pending = h.run('submitAnalysis({preventDefault(){}}, true)');
  assert.equal(h.node('businessId').disabled, true);
  assert.equal(h.node('documentForm-submit').disabled, true);
  assert.equal(h.node('documentForm-submit').attributes.has('aria-busy'), false);
  assert.equal(h.node('dualDocumentForm-submit').attributes.get('aria-busy'), 'true');
  await h.run('submitAnalysis({preventDefault(){}}, false)');
  assert.equal(posts.length, 1);
  h.run('analysisCapabilities.method = "openai_structured"');
  h.node('supplierNameB').value = 'changed';
  h.node('documentFileB').files = [new File(['changed'], 'changed.pdf')];
  resolveFirst({ ok: true, json: async () => result() });
  await pending;
  assert.equal(posts.length, 2);
  assert.equal(posts[1].get('file').name, 'offersB.pdf');
  assert.equal(posts[1].get('supplier_name'), 'SupplierB');
  assert.equal(posts[1].get('analysis_method'), 'openai_blocks_jev');
  assert.equal(posts[1].get('business_id'), 'tenant-one');
  assert.match(h.node('dualStatus').textContent, /Primero guardado/);
  assert.equal(h.node('dualResultMessage').children[0].href, '#detalle/doc%2Fone');
  assert.equal(h.node('businessId').disabled, false);
  assert.equal(h.node('dualDocumentForm-submit').attributes.has('aria-busy'), false);
  assert.equal(h.run('analysisBusy'), false);
});

test('availability failures leave simple messages and retry picks an available method', async () => {
  const h = harness();
  h.context.fetch = async () => { throw new Error('404'); };
  await h.run('loadAnalysisMethods()');
  assert.equal(h.node('documentForm-submit').disabled, true);
  assert.equal(h.node('retryAnalysisMethods').hidden, false);
  h.context.fetch = async () => ({ ok: true, json: async () => ({ contract_version: 'supplier_offer_analysis_v1',
    default_method: 'openai_structured', methods: [{ id: 'openai_structured', available: false },
      { id: 'openai_blocks_jev', available: false }] }) });
  await h.run('loadAnalysisMethods()');
  assert.equal(h.node('documentForm-submit').disabled, true);
  assert.match(h.node('analysisMethodsStatus').textContent, /El comparador no está disponible/);
  assert.equal(h.node('retryAnalysisMethods').hidden, false);
  h.context.fetch = async () => ({ ok: true, json: async () => ({ contract_version: 'supplier_offer_analysis_v1',
    default_method: 'openai_structured', methods: [{ id: 'openai_structured', available: true },
      { id: 'openai_blocks_jev', available: false, unavailable_reason: 'JEV no integrado' }] }) });
  await h.run('loadAnalysisMethods()');
  assert.equal(h.node('documentForm-submit').disabled, false);
  assert.equal(h.node('retryAnalysisMethods').hidden, true);
  assert.equal(h.node('analysisMethodsStatus').textContent, '');
  assert.equal(h.run('captureAnalysisOperation(false).method'), 'openai_structured');
});

test('pending-only results are partial and safe, and history omits providers', () => {
  const h = harness();
  h.context.data = result();
  assert.equal(h.run('analysisNeedsReview(data)'), true);
  assert.match(h.run('summarizeAnalysis(data)'), /Parcial/);
  assert.doesNotMatch(h.run('summarizeAnalysis(data)'), /OpenAI|JEV/);
  h.run('renderDocumentAnalysis(data)');
  assert.match(h.node('pendingBlocksBody').innerHTML, /&lt;img/);
  assert.doesNotMatch(h.node('pendingBlocksBody').innerHTML, /<img/);
  assert.equal(h.run('formatDocumentAnalysis(null)'), 'Resumen no disponible');
  assert.match(h.run('readerAgreementHtml({metadata:{selected_source:"openai",agreement_status:"single_source",agreement_score:1}})'), /Una sola lectura/);
  h.run('renderDualComparison(data, data, "<script>supplier</script>", "B")');
  assert.match(h.node('dualUnmatched').innerHTML, /&lt;script&gt;/);
  assert.match(h.node('dualUnmatched').innerHTML, /&lt;img/);
  assert.match(h.node('dualResultMessage').textContent, /pendientes/);
});

test('persistent detail failure is explicit; no fabricated historical evidence', async () => {
  const h = harness();
  h.run('currentDocumentId = "old/id"');
  h.context.fetch = async (url) => {
    assert.match(url, /old%2Fid\/analysis\?business_id=tenant-one/);
    return { ok: false, json: async () => ({ detail: 'not available' }) };
  };
  await h.run('loadDocumentAnalysis("old/id")');
  assert.match(h.node('analysisDetailSummary').textContent, /No pudimos/);
  assert.equal(h.node('pendingBlocksBody').innerHTML, '');
  assert.equal(h.node('retryDocumentAnalysis').hidden, false);
});

test('tenant, file and name snapshot is independent of all subsequent controls', async () => {
  const h = harness();
  h.context.operation = h.run('captureAnalysisOperation(true)');
  h.node('businessId').value = 'different-tenant';
  h.node('supplierNameB').value = 'different-supplier';
  h.node('documentFileB').files = [];
  h.run('analysisCapabilities.method = "openai_blocks_jev"');
  h.context.fetch = async (url, options) => {
    assert.equal(options.body.get('business_id'), 'tenant-one');
    assert.equal(options.body.get('supplier_name'), 'SupplierB');
    assert.equal(options.body.get('analysis_method'), 'openai_structured');
    assert.equal(options.body.get('file').name, 'offersB.pdf');
    return { ok: true, json: async () => result() };
  };
  await h.run('analyzePdf(operation.uploads[1].file, operation.uploads[1].name, operation)');
});

test('successful single and double pending-only uploads remain visibly partial', async () => {
  for (const dual of [false, true]) {
    const h = harness();
    let calls = 0;
    h.context.fetch = async () => ({ ok: true, json: async () => result(`doc-${++calls}`) });
    await h.run(`submitAnalysis({preventDefault(){}}, ${dual})`);
    assert.equal(calls, dual ? 2 : 1);
    assert.equal(h.node(dual ? 'dualStatus' : 'documentStatus').textContent, 'Parcial');
    assert.equal(h.node('businessId').disabled, false);
    if (dual) assert.match(h.node('dualUnmatched').innerHTML, /&lt;img/);
  }
});

test('saved analysis status is displayed even for documents outside loaded history', async () => {
  const h = harness();
  h.run('currentDocumentId = "old/id"');
  h.context.fetch = async () => ({ ok: true, json: async () => ({
    document_id: 'old/id', analysis: result().analysis, pending_blocks: result().pending_blocks,
  }) });
  await h.run('loadDocumentAnalysis("old/id")');
  assert.match(h.node('detailSubtitle').textContent, /Parcial/);
  assert.doesNotMatch(h.node('detailSubtitle').textContent, /OpenAI|JEV/);
  assert.equal(h.node('retryDocumentAnalysis').hidden, true);
});


test('only the visible confidence setting is sent to both PDF requests', async () => {
  const h = harness();
  assert.equal(h.run('captureAnalysisOperation(false).thresholds.decision_confidence'), 0.85);
  assert.doesNotMatch(html, /id="(?:analysisMethod|identityProbability|priceProbability|decisionMargin)"/);
  h.node('decisionConfidence').value = '88.5';
  h.run('operation = captureAnalysisOperation(true)');
  h.node('decisionConfidence').value = '50';
  h.context.fetch = async (url, options) => {
    assert.equal(options.body.get('identity_probability'), null);
    assert.equal(options.body.get('price_probability'), null);
    assert.equal(options.body.get('decision_confidence'), '0.885');
    assert.equal(options.body.get('decision_margin'), null);
    return { ok: true, json: async () => result() };
  };
  await h.run('Promise.all(operation.uploads.map(u => analyzePdf(u.file, u.name, operation)))');
});

test('rejects missing and invalid confidence before sending', () => {
  const h = harness();
  for (const value of ['', 'NaN', '-1', '101']) {
    h.node('decisionConfidence').value = value;
    assert.throws(() => h.run('captureAnalysisOperation(false)'), /confianza/);
  }
});
