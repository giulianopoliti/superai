const { chromium } = require('C:/Users/54116/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/ui/')) {
      const name = path.basename(url.pathname);
      return route.fulfill({ body: fs.readFileSync(path.join('app/static', name)), contentType: name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'text/javascript' : name.endsWith('.png') ? 'image/png' : 'text/html' });
    }
    let json = {};
    if (url.pathname.endsWith('/summaries')) json = [];
    if (url.pathname.endsWith('/analysis-methods')) json = { default_method: 'openai_structured', methods: [{ id: 'openai_structured', available: true }] };
    if (url.pathname.endsWith('/dashboard')) json = { sales_status: {}, sales_totals: { revenue: '1350400', quantity_sold: '582', ticket_count: 178 }, alerts: [], pending_reminders: [], overdue_reminders: [], supplier_offers: [], top_products_by_quantity: [{ product_name: 'Yerba mate 1 kg', quantity_sold: '54', revenue: '280800' }] };
    return route.fulfill({ json });
  });
  await page.goto('http://127.0.0.1:8767/ui/index.html');
  await page.waitForFunction(() => document.querySelector('.brandLogo').naturalWidth > 0);
  const widths = [];
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 950 });
    for (const view of ['inicio', 'comparaciones', 'chat', 'datos']) {
      await page.locator(`[data-nav="${view}"]`).click();
      await page.locator(`#view-${view}`).waitFor({ state: 'visible' });
      widths.push({ width, view, scrollWidth: await page.evaluate(() => document.documentElement.scrollWidth) });
      if (width !== 320) await page.screenshot({ path: `tmp/salvador-${view}-${width}.png`, fullPage: true });
    }
  }
  console.log(JSON.stringify({ errors, widths }));
  await browser.close();
  if (errors.length || widths.some(x => x.scrollWidth > x.width)) process.exitCode = 1;
})().catch(e => { console.error(e); process.exit(1); });
