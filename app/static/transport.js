(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  else root.StockTransport = factory({
    config: root.STOCK_AI_CONFIG,
    fetch: (...args) => root.fetch(...args),
    getToken: () => root.StockAuth.accessToken(),
    unauthorized: () => root.StockAuth.expired(),
  });
})(typeof window !== "undefined" ? window : globalThis, function createTransport(deps) {
  let generation = 0;
  const controllers = new Set();
  const config = deps.config || { authEnabled: true, apiBaseUrl: "/api" };
  if (!["", "/api"].includes(config.apiBaseUrl)) throw new Error("Configuración de API inválida.");

  function internalPath(path) {
    if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") || /[\\\r\n#]/.test(path)) {
      throw new Error("Ruta de API inválida.");
    }
    if (path.startsWith("/api/")) path = path.slice(4);
    const pathname = decodeURIComponent(path.split("?", 1)[0]);
    if (pathname.includes("\\") || pathname.split("/").includes("..")) throw new Error("Ruta de API inválida.");
    if (!/^\/(auth|operations|sales|procurement|assistant)\//.test(path)) throw new Error("Ruta de API inválida.");
    return path;
  }

  async function request(path, options = {}) {
    path = internalPath(path);
    const epoch = generation;
    const controller = new AbortController();
    controllers.add(controller);
    const abort = () => controller.abort();
    options.signal?.addEventListener("abort", abort, { once: true });
    try {
      const headers = new Headers(options.headers);
      headers.delete("X-Stock-AI-Origin");
      if (config.authEnabled) headers.set("Authorization", `Bearer ${await deps.getToken()}`);
      if (generation !== epoch || options.signal?.aborted) throw new DOMException("Sesión cambiada", "AbortError");
      const response = await deps.fetch(config.apiBaseUrl + path, {
        ...options, headers, signal: controller.signal, credentials: "omit", redirect: "error", cache: "no-store",
      });
      if (generation !== epoch) throw new DOMException("Sesión cambiada", "AbortError");
      if (response.status === 401 && config.authEnabled) deps.unauthorized();
      // Headers can arrive before the body. Also reject stale JSON/blob reads.
      return new Proxy(response, { get(target, key) {
        if (["json", "blob", "text", "arrayBuffer"].includes(key)) return async (...args) => {
          const value = await target[key](...args);
          if (generation !== epoch) throw new DOMException("Sesión cambiada", "AbortError");
          return value;
        };
        const value = Reflect.get(target, key, target);
        return typeof value === "function" ? value.bind(target) : value;
      } }); // Never refresh/repeat an accepted or uncertain POST here.
    } finally {
      controllers.delete(controller);
      options.signal?.removeEventListener("abort", abort);
    }
  }

  return {
    fetch: request, internalPath,
    cancelAll() { generation++; for (const controller of controllers) controller.abort(); controllers.clear(); },
  };
});
