const fs = require("node:fs");
const path = require("node:path");
const esbuild = require("esbuild");
const root = path.resolve(__dirname, "..");

function origin(value, name) {
  let url;
  try { url = new URL(value); } catch { throw new Error(`${name} debe contener un origen HTTPS.`); }
  if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new Error(`${name} debe ser un origen HTTPS fijo.`);
  return url.origin;
}
async function build() {
  const supabaseUrl = origin(process.env.SUPABASE_URL, "SUPABASE_URL");
  const upstream = origin(process.env.AWS_API_ORIGIN, "AWS_API_ORIGIN");
  const key = process.env.SUPABASE_PUBLISHABLE_KEY || "";
  if (!key.startsWith("sb_publishable_")) throw new Error("Se requiere una clave publicable de Supabase.");
  if (!process.env.AWS_ORIGIN_SECRET || process.env.AWS_ORIGIN_SECRET.length < 32) throw new Error("Falta el secreto de origen del servidor.");
  if (process.env.VERCEL_ENV === "preview" && upstream === "https://stockiando.duckdns.org") throw new Error("La preview requiere un backend de pruebas aislado.");
  const output = path.join(root, ".vercel/output");
  // This fixed generated directory is never a caller-supplied path.
  if (path.dirname(output) !== path.join(root, ".vercel")) throw new Error("Destino de build inválido.");
  fs.rmSync(output, { recursive: true, force: true });
  const ui = path.join(output, "static/ui");
  fs.mkdirSync(ui, { recursive: true });
  const assets = ["index.html", "app.js", "styles.css", "super-salvador.png", "transport.js", "jobs.js"];
  for (const asset of assets) fs.copyFileSync(path.join(root, "app/static", asset), path.join(ui, asset));
  const config = { authEnabled: true, jobsEnabled: true, apiBaseUrl: "/api", supabaseUrl, supabasePublishableKey: key };
  fs.writeFileSync(path.join(ui, "config.js"), "window.STOCK_AI_CONFIG = Object.freeze(" + JSON.stringify(config).replace(/</g, "\\u003c") + ");\n");
  await esbuild.build({ entryPoints: [path.join(root, "frontend/auth-entry.mjs")], bundle: true,
    minify: true, platform: "browser", format: "iife", target: "es2020", outfile: path.join(ui, "auth.js") });
  fs.writeFileSync(path.join(output, "static/index.html"), '<!doctype html><html lang="es-AR"><meta charset="utf-8"><title>Stock AI</title><script src="/redirect.js" defer></script><a href="/ui/">Ingresar</a></html>');
  fs.writeFileSync(path.join(output, "static/redirect.js"), 'location.replace("/ui/" + location.search + location.hash);\n');
  const csp = `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self' ${supabaseUrl}; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'`;
  const noCache = { "Cache-Control": "no-store", "CDN-Cache-Control": "no-store", "Vercel-CDN-Cache-Control": "no-store", "x-vercel-enable-rewrite-caching": "0" };
  const proxy = { src: "^/api/(.*)$", dest: upstream + "/$1", headers: noCache,
    transforms: [{ type: "request.headers", op: "set", target: { key: "x-stock-ai-origin" }, args: "$AWS_ORIGIN_SECRET", env: ["AWS_ORIGIN_SECRET"] }] };
  const routes = [proxy,
    { src: "^/api$", status: 404 },
    { src: "^/procurement-ui/?$", dest: "/index.html" },
    { src: "/(.*)", headers: { ...noCache, "Content-Security-Policy": csp,
      "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin" }, continue: true },
    { src: "^/ui/?$", dest: "/ui/index.html" },
    { handle: "filesystem" }, { src: "/(.*)", status: 404 }];
  fs.writeFileSync(path.join(output, "config.json"), JSON.stringify({ version: 3, routes }, null, 2) + "\n");
  const publicFiles = fs.readdirSync(ui).map((name) => path.join(ui, name));
  for (const filename of publicFiles.filter((filename) => !filename.endsWith(".png"))) {
    if (fs.readFileSync(filename, "utf8").includes(process.env.AWS_ORIGIN_SECRET)) throw new Error("Secreto detectado en un archivo público.");
  }
  console.log("Build estático generado en .vercel/output; secretos solo por transformación servidor.");
}
build().catch((error) => { console.error(error.message); process.exitCode = 1; });
