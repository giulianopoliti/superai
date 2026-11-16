import { createClient } from "@supabase/supabase-js";

const config = window.STOCK_AI_CONFIG;
const node = (id) => document.getElementById(id);
const shell = document.querySelector(".appShell");
const listeners = new Set();
let client, context, refreshing, closing = false;
let authorizationVersion = 0;
const storageKey = "stock-ai.auth.v1";
const storage = window.sessionStorage;

function status(message) { node("loginStatus").textContent = message; }

function showSessionCheck() {
  shell.hidden = true;
  node("logoutButton").hidden = true;
  node("loginForm").hidden = true;
  node("sessionCheck").hidden = false;
  node("loginPanel").hidden = false;
}

function showLogin(message = "") {
  shell.hidden = true;
  node("logoutButton").hidden = true;
  node("sessionCheck").hidden = true;
  node("loginForm").hidden = false;
  node("loginPanel").hidden = false;
  status(message);
}

async function endSession(message) {
  if (closing) return;
  closing = true;
  authorizationVersion++;
  window.StockTransport.cancelAll();
  window.StockJobs?.stop();
  context = null;
  node("productSearchDialog")?.close?.();
  showLogin(message);
  try { await client?.auth.signOut({ scope: "local" }); } catch { /* Local cleanup still applies. */ }
  storage.removeItem(storageKey);
  storage.setItem("stock-ai.login-message", message);
  window.location.reload(); // Clear private DOM, dialogs and all in-memory business data.
}

async function accessToken() {
  if (!client || closing) throw new Error("Ingresá para continuar.");
  let { data, error } = await client.auth.getSession();
  if (error || !data.session) {
    if (context) void endSession("La sesión venció. Ingresá nuevamente.");
    throw new Error("Ingresá para continuar.");
  }
  if (data.session.expires_at <= Date.now() / 1000 + 30) {
    refreshing ||= client.auth.refreshSession().finally(() => { refreshing = null; });
    const refreshed = await refreshing;
    if (refreshed.error || !refreshed.data.session) {
      void endSession("La sesión venció. Ingresá nuevamente.");
      throw new Error("La sesión venció. Ingresá nuevamente.");
    }
    data = refreshed.data;
  }
  return data.session.access_token;
}

async function authorize() {
  const version = ++authorizationVersion;
  const { data: initial } = await client.auth.getSession();
  if (!initial.session) throw new Error("Ingresá nuevamente.");
  const response = await window.StockTransport.fetch("/auth/me");
  if (!response.ok) throw new Error(response.status === 403 ? "Tu usuario no tiene acceso a este comercio." : "No se pudo validar la sesión.");
  const profile = await response.json();
  if (!profile.businesses?.length) throw new Error("Tu usuario no tiene comercios habilitados.");
  const { data } = await client.auth.getSession();
  if (closing || version !== authorizationVersion) return;
  if (!data.session || data.session.user.id !== initial.session.user.id) throw new Error("Ingresá nuevamente.");
  const selected = storage.getItem("stock-ai.business.v1");
  const access = profile.businesses.find((row) => row.business_id === selected) || profile.businesses[0];
  context = { profile, subject: data.session.user.id, businessId: access.business_id };
  node("businessId").value = context.businessId;
  node("businessId").readOnly = profile.businesses.length === 1;
  node("authorizedBusinesses").replaceChildren(...profile.businesses.map((business) => {
    const option = document.createElement("option"); option.value = business.business_id; option.label = business.business_name; return option;
  }));
  node("sessionName").textContent = access.display_name;
  node("loginPanel").hidden = true;
  node("sessionCheck").hidden = true;
  node("logoutButton").hidden = false;
  shell.hidden = false;
  node("loginPassword").value = "";
  for (const listener of listeners) listener();
}

window.StockAuth = {
  accessToken,
  scope: () => context ? `${context.subject}:${context.businessId}` : "none",
  expired: () => { void endSession("La sesión venció. Ingresá nuevamente."); },
  onReady(listener) { listeners.add(listener); if (context) listener(); },
  selectBusiness(value) {
    if (!context?.profile.businesses.some((row) => row.business_id === value)) {
      if (context) node("businessId").value = context.businessId;
      return false;
    }
    window.StockTransport.cancelAll(); window.StockJobs.stop();
    context.businessId = value;
    storage.setItem("stock-ai.business.v1", value);
    return true;
  },
};

node("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!client) return;
  const button = node("loginSubmit"); button.disabled = true; status("Ingresando…");
  try {
    const { error } = await client.auth.signInWithPassword({ email: node("loginEmail").value.trim(), password: node("loginPassword").value });
    node("loginPassword").value = "";
    if (error) throw new Error("No pudimos ingresar. Revisá el correo y la contraseña.");
    await authorize();
  } catch (error) { showLogin(error.message); } finally { button.disabled = false; }
});
node("logoutButton").addEventListener("click", () => { void endSession("Cerraste sesión."); });

// A browser history snapshot can retain a private DOM after logout in another
// page. Hide it before freezing and force server authorization on restoration.
window.addEventListener("pagehide", () => {
  authorizationVersion++;
  shell.hidden = true;
  window.StockTransport.cancelAll();
  window.StockJobs?.stop();
  node("productSearchDialog")?.close?.();
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    showSessionCheck();
    window.location.reload();
  }
});

async function initialize() {
  showSessionCheck();
  if (!config?.authEnabled || !config.supabaseUrl || !config.supabasePublishableKey) {
    showLogin("El acceso todavía no está configurado."); return;
  }
  client = createClient(config.supabaseUrl, config.supabasePublishableKey, { auth: {
    storage, storageKey, persistSession: true, autoRefreshToken: true, detectSessionInUrl: false,
  } });
  client.auth.onAuthStateChange((event) => {
    // Supabase callbacks remain synchronous; session methods run after its lock releases.
    if (event === "SIGNED_OUT" && context && !closing) setTimeout(() => { void endSession("La sesión venció. Ingresá nuevamente."); }, 0);
  });
  const message = storage.getItem("stock-ai.login-message") || "";
  storage.removeItem("stock-ai.login-message");
  try {
    const { data, error } = await client.auth.getSession();
    if (error) throw error;
    if (data.session) await authorize();
    else showLogin(message);
  }
  catch { showLogin("No se pudo restaurar la sesión. Ingresá nuevamente."); }
}
void initialize();
