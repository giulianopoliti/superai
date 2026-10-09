const { chromium } = require("C:/Users/54116/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  });
  const errors = [];
  const documents = [];
  let uploadCount = 0;
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/operations/dashboard?*", (route) => route.fulfill({ json: {
    business_id: "demo-business", date_from: "2026-09-26", date_to: "2026-10-02",
    sales_status: { last_sale_date: "2026-10-01" },
    sales_totals: { revenue: "1350400", quantity_sold: "582", ticket_count: 178 },
    alerts: [{ severity: "warning", title: "Productos sin costo", description: "14 productos necesitan un costo." }],
    pending_reminders: [], overdue_reminders: [], supplier_offers: [],
    top_products_by_quantity: [
      { product_name: "Aceite girasol 900 ml", quantity_sold: "76", revenue: "190000" },
      { product_name: "Yerba mate 1 kg", quantity_sold: "54", revenue: "280800" },
    ],
  }}));
  await page.route("**/procurement/supplier-offers/summaries?*", (route) => route.fulfill({ json: documents }));
  await page.route("**/procurement/supplier-offers/from-document", (route) => {
    uploadCount += 1;
    const id = `00000000-0000-0000-0000-00000000000${uploadCount}`;
    const name = uploadCount === 1 ? "Vital" : "Diarco";
    documents.unshift({ document: { id, source_filename: `${name}.pdf`, created_at: "2026-10-02T10:00:00Z" }, supplier_name: name, item_count: 1, candidate_count: 1, matched_count: 1, pending_count: 0 });
    route.fulfill({ json: {
      import_result: { document: { id, metadata: {} }, items: [{ id: `offer-${uploadCount}` }] },
      comparison: { persisted_count: 1, report: { matched_count: 1, candidates: [{
        auto_accepted: true, product: { id: "product-1", name: "Yerba mate 1 kg", sku: "YERBA", current_cost: "5000" },
        supplier_offer_item: { raw_name: "Yerba 1 kg", offer_price: uploadCount === 1 ? "4400" : "4200", comparable_unit_price: uploadCount === 1 ? "4400" : "4200", price_type: "unit", currency: "ARS", tax_included: true },
        recommendation: "buy",
      }] } },
    } });
  });
  await page.route("**/procurement/supplier-offers/*/matches?*", (route) => route.fulfill({ json: { candidates: [] } }));
  await page.route("**/procurement/supplier-offers/*/purchase-plan?*", (route) => route.fulfill({ json: { items: [], target_coverage_days: null, date_from: "2026-09-26", date_to: "2026-10-02", sort_by: "sales_then_savings" } }));
  await page.route("**/sales/status?*", (route) => route.fulfill({ json: { last_sale_date: "2026-10-01", first_sale_date: "2026-01-01", total_sale_lines: 2000, last_imported_at: "2026-10-02T10:00:00Z" } }));
  await page.route("**/procurement/catalog-imports/status?*", (route) => route.fulfill({ json: { total_products: 3000, last_import: { completed_at: "2026-10-01T10:00:00Z", source_filename: "productos.csv", summary: { created_count: 10, updated_count: 50, unchanged_count: 2940 } } } }));
  await page.route("**/assistant/message", (route) => route.fulfill({ json: { reply: "Vendiste 582 unidades en los últimos siete días.", metadata: { tool_data: { products: [] } } } }));
  await page.goto("http://127.0.0.1:8001/ui/index.html");
  await page.waitForSelector("#operationsRevenue:text('1.350.400')");
  await page.screenshot({ path: "tmp/ui-home.png", fullPage: true });
  await page.click('a[href="#comparaciones"]');
  await page.waitForSelector("#view-comparaciones:not([hidden])");
  await page.click('[data-mode="dual"]');
  await page.waitForSelector("#dualComparison:not([hidden])");
  await page.screenshot({ path: "tmp/ui-comparisons.png", fullPage: true });
  await page.fill("#supplierNameA", "Vital");
  await page.fill("#supplierNameB", "Diarco");
  await page.setInputFiles("#documentFileA", { name: "Vital.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") });
  await page.setInputFiles("#documentFileB", { name: "Diarco.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") });
  await page.click('#dualDocumentForm button[type="submit"]');
  await page.waitForSelector("#dualResult:not([hidden])");
  if (!(await page.textContent("#dualResultBody")).includes("Diarco")) throw new Error("Dual comparison winner missing");
  await page.screenshot({ path: "tmp/ui-dual-result.png", fullPage: true });
  await page.click('#documentsBody button[data-action="open-document"]');
  await page.waitForSelector("#view-detalle:not([hidden])");
  await page.screenshot({ path: "tmp/ui-detail.png", fullPage: true });
  await page.click('a[href="#chat"]');
  await page.waitForSelector("#view-chat:not([hidden])");
  await page.click('[data-chat-suggestion]');
  await page.waitForSelector(".message.assistant");
  await page.screenshot({ path: "tmp/ui-chat.png", fullPage: true });
  await page.click('a[href="#datos"]');
  await page.waitForSelector("#view-datos:not([hidden])");
  await page.screenshot({ path: "tmp/ui-data.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("http://127.0.0.1:8001/ui/index.html#inicio");
  await page.screenshot({ path: "tmp/ui-mobile.png", fullPage: true });
  const mobileWidths = {};
  for (const view of ["inicio", "comparaciones", "chat", "datos"]) {
    await page.goto(`http://127.0.0.1:8001/ui/index.html#${view}`);
    mobileWidths[view] = await page.evaluate(() => document.documentElement.scrollWidth);
  }
  console.log(JSON.stringify({ errors, views: 5, dualUploads: uploadCount, mobileWidths, mobileViewport: 390 }));
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
