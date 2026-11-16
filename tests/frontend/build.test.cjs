const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

test('static build keeps business traffic same-origin and origin secret server-only', () => {
  const secret = 'build-test-secret-never-public-0123456789';
  const build = spawnSync(process.execPath, ['scripts/build_frontend.cjs'], { encoding: 'utf8', env: {
    ...process.env, SUPABASE_URL: 'https://example.supabase.co', SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_local_test',
    AWS_API_ORIGIN: 'https://backend-test.example.com', AWS_ORIGIN_SECRET: secret, VERCEL_ENV: 'preview',
  } });
  assert.equal(build.status, 0, build.stderr);
  const root = '.vercel/output';
  const config = JSON.parse(fs.readFileSync(`${root}/config.json`, 'utf8'));
  assert.equal(config.version, 3);
  const proxy = config.routes[0];
  assert.equal(proxy.dest, 'https://backend-test.example.com/$1');
  assert.deepEqual(proxy.transforms, [{ type: 'request.headers', op: 'set', target: { key: 'x-stock-ai-origin' }, args: '$AWS_ORIGIN_SECRET', env: ['AWS_ORIGIN_SECRET'] }]);
  assert.equal(proxy.headers['Cache-Control'], 'no-store');
  assert.equal(proxy.headers['CDN-Cache-Control'], 'no-store');
  assert.equal(proxy.headers['Vercel-CDN-Cache-Control'], 'no-store');
  assert.equal(proxy.headers['x-vercel-enable-rewrite-caching'], '0');
  const staticRoot = `${root}/static`;
  for (const file of fs.readdirSync(staticRoot, { recursive: true })) {
    const filename = path.join(staticRoot, file);
    if (fs.statSync(filename).isFile()) {
      const contents = fs.readFileSync(filename);
      assert.equal(contents.includes(secret), false, file);
      assert.equal(contents.includes('backend-test.example.com'), false, file);
      assert.doesNotMatch(file, /\.env|\.pem|\.pdf|\.csv|manifest|\.map$/);
    }
  }
  const publicConfig = fs.readFileSync(`${staticRoot}/ui/config.js`, 'utf8');
  assert.match(publicConfig, /"apiBaseUrl":"\/api"/);
  assert.match(publicConfig, /"authEnabled":true/);
  assert.match(publicConfig, /"jobsEnabled":true/);
  assert.deepEqual(fs.readdirSync(`${staticRoot}/ui`).sort(), ['app.js','auth.js','config.js','index.html','jobs.js','styles.css','super-salvador.png','transport.js']);
  assert.match(fs.readFileSync(`${staticRoot}/redirect.js`, 'utf8'), /location.search \+ location.hash/);
  const fallback = config.routes.at(-1);
  assert.equal(fallback.status, 404);
});

test('build refuses an HTTP upstream and preview against the production backend', () => {
  for (const origin of ['http://13.59.144.233', 'https://stockiando.duckdns.org']) {
    const build = spawnSync(process.execPath, ['scripts/build_frontend.cjs'], { encoding: 'utf8', env: {
      ...process.env, SUPABASE_URL: 'https://example.supabase.co', SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_test',
      AWS_API_ORIGIN: origin, AWS_ORIGIN_SECRET: 'build-test-secret-never-public-0123456789', VERCEL_ENV: 'preview',
    } });
    assert.notEqual(build.status, 0);
  }
});
