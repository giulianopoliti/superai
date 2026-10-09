const $ = (id) => document.getElementById(id);

let currentMatches = [];
let currentDocumentId = "";
let currentPurchasePlan = null;
let documentSummaries = [];
let correctionCandidateId = "";
let chatBusy = false;
let analysisBusy = false;
let analysisCapabilities = null;
let capabilityRequest = 0;
const analysisMethods = Object.freeze({
  openai_structured: "OpenAI separa los datos",
  openai_blocks_jev: "OpenAI lee y JEV organiza",
});
let lockedAnalysisControls = [];
const documentAnalyses = new Map();

function analysisControls() {
  return [...document.querySelectorAll(
    '.methodSurface input, #documentForm input, #documentForm button, #dualDocumentForm input, #dualDocumentForm button, [data-mode]',
  ), $("analysisMethod"), $("businessId"), $("retryAnalysisMethods")];
}

function refreshAnalysisAvailability() {
  const select = $("analysisMethod");
  for (const option of select.options) {
    option.disabled = !analysisCapabilities?.methods.some((method) =>
      method.id === option.value && method.available === true);
  }
  if (select.selectedOptions[0]?.disabled) {
    select.value = analysisCapabilities?.methods.find((method) =>
      Object.hasOwn(analysisMethods, method.id) && method.available === true)?.id || "openai_structured";
  }
  const available = analysisCapabilities?.methods.some((method) =>
    method.id === select.value && method.available === true);
  select.disabled = analysisBusy || !available;
  for (const formId of ["documentForm", "dualDocumentForm"]) {
    $(formId).querySelector('button[type="submit"]').disabled = analysisBusy || !available;
  }
  $("analysisMethodHelp").textContent = select.value === "openai_blocks_jev"
    ? "OpenAI lee los textos; JEV propone cómo organizarlos y el sistema valida."
    : "OpenAI separa los datos; el sistema valida y consulta JEV si quedan dudas.";
}

async function loadAnalysisMethods() {
  const request = ++capabilityRequest;
  const tenant = businessId();
  analysisCapabilities = null;
  refreshAnalysisAvailability();
  $("retryAnalysisMethods").hidden = true;
  $("analysisMethodsStatus").textContent = "Consultando métodos disponibles…";
  try {
    const data = await requestJson(`/procurement/supplier-offers/analysis-methods?business_id=${encodeURIComponent(tenant)}`);
    if (request !== capabilityRequest || tenant !== businessId()) return;
    if (data.contract_version !== "supplier_offer_analysis_v1" || !Array.isArray(data.methods) ||
        !Object.hasOwn(analysisMethods, data.default_method) ||
        Object.keys(analysisMethods).some((id) => data.methods.filter((method) => method.id === id).length !== 1) ||
        data.methods.some((method) => typeof method.available !== "boolean")) {
      throw new Error("Respuesta de métodos no compatible.");
    }
    analysisCapabilities = { ...data, businessId: tenant };
    const reasons = data.methods.filter((method) => Object.hasOwn(analysisMethods, method.id) && !method.available)
      .map((method) => `${analysisMethods[method.id]}: ${method.unavailable_reason || "No disponible."}`);
    $("analysisMethodsStatus").textContent = reasons.join(" ") || "Ambos métodos disponibles. Elegí uno para los PDF de esta operación.";
  } catch (error) {
    if (request !== capabilityRequest || tenant !== businessId()) return;
    $("analysisMethodsStatus").textContent = "No pudimos consultar los métodos disponibles. Reintentá antes de enviar.";
    $("retryAnalysisMethods").hidden = false;
  }
  refreshAnalysisAvailability();
}

function setAnalysisBusy(busy) {
  analysisBusy = busy;
  if (busy) {
    lockedAnalysisControls = analysisControls().map((node) => [node, node.disabled]);
    lockedAnalysisControls.forEach(([node]) => { node.disabled = true; });
  } else {
    lockedAnalysisControls.forEach(([node, disabled]) => { node.disabled = disabled; });
    lockedAnalysisControls = [];
    refreshAnalysisAvailability();
  }
}

function captureAnalysisOperation(dual) {
  const tenant = businessId();
  const method = $("analysisMethod").value;
  if (analysisCapabilities?.businessId !== tenant || !Object.hasOwn(analysisMethods, method) ||
      !analysisCapabilities.methods.some((entry) => entry.id === method && entry.available === true)) {
    throw new Error("Consultá los métodos disponibles antes de enviar.");
  }
  const thresholds = {};
  for (const [id, key] of [
    ["identityProbability", "identity_probability"],
    ["priceProbability", "price_probability"],
    ["decisionConfidence", "decision_confidence"],
    ["decisionMargin", "decision_margin"],
  ]) {
    const input = $(id);
    const value = Number(input.value);
    if (input.value.trim() === "" || !Number.isFinite(value) || value < 0 || value > 100) {
      throw new Error("Los umbrales deben ser porcentajes entre 0 y 100.");
    }
    thresholds[key] = value / 100;
  }
  const uploads = dual ? [
    { file: $("documentFileA").files[0], name: $("supplierNameA").value.trim() },
    { file: $("documentFileB").files[0], name: $("supplierNameB").value.trim() },
  ] : [{ file: $("documentFile").files[0], name: $("supplierName").value.trim() }];
  for (const upload of uploads) {
    if (!upload.file || !upload.file.name.toLowerCase().endsWith(".pdf")) {
      throw new Error("Elegí un archivo PDF para cada proveedor.");
    }
    if (!upload.name) throw new Error("Completá el nombre de cada proveedor.");
  }
  return Object.freeze({ businessId: tenant, method, thresholds: Object.freeze(thresholds), uploads: uploads.map((upload) => Object.freeze(upload)) });
}

function formatDocumentAnalysis(analysis) {
  if (!analysis) return "Método no registrado";
  const method = analysisMethods[analysis.method] || "Método no registrado";
  const linked = Number.isInteger(analysis.interpreted_offer_count) && Number.isInteger(analysis.unlinked_offer_count)
    ? Math.max(0, analysis.interpreted_offer_count - analysis.unlinked_offer_count) : "—";
  const coverage = Number.isInteger(analysis.read_page_count) && Number.isInteger(analysis.page_count)
    ? `${analysis.read_page_count}/${analysis.page_count} páginas con resultado · ` : "";
  const unread = Array.isArray(analysis.unattempted_page_numbers) && analysis.unattempted_page_numbers.length
    ? `${analysis.unattempted_page_numbers.length} páginas sin enviar · ` : "";
  const failed = Array.isArray(analysis.failed_page_numbers) && analysis.failed_page_numbers.length
    ? `${analysis.failed_page_numbers.length} páginas intentadas sin resultado · ` : "";
  return `${method} · ${analysis.status === "partial" ? "Parcial" : "Guardado"} · ` +
    coverage + unread + failed +
    `${analysis.pending_block_count ?? "—"} bloques pendientes · ${linked} ofertas vinculadas · ${analysis.unlinked_offer_count ?? "—"} ofertas sin vínculo · ` +
    `${analysis.identity_review_offer_count ?? "—"} identidades y ${analysis.price_review_offer_count ?? "—"} bases de precio para revisar`;
}

function resultAnalysis(data) {
  return data.analysis || data.import_result.document.metadata?.analysis || null;
}

function analysisNeedsReview(data) {
  const analysis = resultAnalysis(data);
  return analysis?.status === "partial" || ["pending_block_count", "unlinked_offer_count",
    "identity_review_offer_count", "price_review_offer_count"].some((key) => Number(analysis?.[key]) > 0) ||
    (data.pending_blocks || []).length > 0 ||
    (data.import_result.document.metadata?.needs_review_pages || []).length > 0 ||
    (data.comparison?.report?.candidates || []).some((candidate) => !candidate.auto_accepted);
}

function pendingBlocksHtml(blocks) {
  return blocks.map((block) => `<article class="pendingBlock"><strong>Página ${escapeHtml(block.page_number)}</strong>` +
    `<p>${escapeHtml(block.text)}</p><small>${escapeHtml(block.reason || (block.reason_codes || []).join(", "))}</small></article>`).join("");
}

function renderDocumentAnalysis(data) {
  $("analysisDetailSummary").textContent = formatDocumentAnalysis(data.analysis);
  $("pendingBlocksBody").innerHTML = pendingBlocksHtml(data.pending_blocks || []) ||
    (data.analysis?.pending_block_count > 0 ? '<p>El resumen registra bloques pendientes, pero sus textos no están disponibles en esta respuesta.</p>' :
      data.analysis ? '<p>No hay bloques pendientes registrados.</p>' : '<p>Este documento no tiene un registro de análisis.</p>');
}

async function loadDocumentAnalysis(documentId) {
  const tenant = businessId();
  $("pendingBlocksBody").textContent = "";
  $("analysisDetailSummary").textContent = "Consultando análisis guardado…";
  $("retryDocumentAnalysis").hidden = true;
  try {
    const data = await requestJson(`/procurement/supplier-offers/${encodeURIComponent(documentId)}/analysis?business_id=${encodeURIComponent(tenant)}`);
    if (currentDocumentId !== documentId || businessId() !== tenant) return;
    documentAnalyses.set(`${tenant}:${documentId}`, data);
    renderDocumentAnalysis(data);
    const selected = documentSummaries.find((item) => item.document.id === documentId);
    $("detailSubtitle").textContent = `${selected?.document.source_filename || "Lectura guardada"} · ${formatDocumentAnalysis(data.analysis)}`;
  } catch (error) {
    if (currentDocumentId !== documentId || businessId() !== tenant) return;
    const saved = documentAnalyses.get(`${tenant}:${documentId}`);
    if (saved) renderDocumentAnalysis(saved);
    $("analysisDetailSummary").textContent = (saved ? `${formatDocumentAnalysis(saved.analysis)}. ` : "") +
      "No pudimos consultar el detalle persistente. Reintentá la consulta.";
    $("retryDocumentAnalysis").hidden = false;
  }
}

function lastSevenDays() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Argentina/Buenos_Aires",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date()).map((part) => [part.type, part.value]));
  const dateTo = `${parts.year}-${parts.month}-${parts.day}`;
  const first = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day) - 6));
  return { dateFrom: first.toISOString().slice(0, 10), dateTo };
}

function showView(view) {
  const resolved = ["inicio", "comparaciones", "detalle", "chat", "datos"].includes(view)
    ? view : "inicio";
  document.querySelectorAll(".view").forEach((node) => {
    node.hidden = node.dataset.view !== resolved;
  });
  document.querySelectorAll("[data-nav]").forEach((node) => {
    if (node.dataset.nav === (resolved === "detalle" ? "comparaciones" : resolved)) {
      node.setAttribute("aria-current", "page");
    } else {
      node.removeAttribute("aria-current");
    }
  });
  window.scrollTo({ top: 0, behavior: "instant" });
}

function routeFromHash() {
  const [view, documentId] = location.hash.slice(1).split("/");
  showView(view || "inicio");
  if (view === "detalle" && documentId && documentId !== currentDocumentId) {
    openDocument(documentId).catch((error) => {
      $("purchasePlanSummary").textContent = friendlyError(error.message);
    });
  }
}

function setComparisonMode(mode) {
  if (analysisBusy) return;
  $("singleComparison").hidden = mode !== "single";
  $("dualComparison").hidden = mode !== "dual";
  document.querySelectorAll("[data-mode]").forEach((button) => {
    const selected = button.dataset.mode === mode;
    button.classList.toggle("isSelected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
}

function businessId() {
  return $("businessId").value.trim() || "demo-business";
}

function setStatus(id, text, kind = "") {
  const node = $(id);
  node.textContent = text;
  node.className = `status ${kind}`.trim();
}

function money(value) {
  if (value === null || value === undefined || value === "") return "";
  return Number(value).toLocaleString("es-AR", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 0,
  });
}

function supplierPriceHtml(offer) {
  const quoted = money(offer.offer_price);
  const comparable = offer.comparable_unit_price;
  const packReview = offer.metadata?.pack_review;
  if (packReview?.status === "confirmed" && offer.price_type === "unit") {
    return `${quoted}<br><small title="${escapeHtml(packReview.decision?.reason)}">Precio por envase verificado</small>`;
  }
  if (comparable === null || comparable === undefined) {
    return `${quoted}<br><small>Base a revisar</small>`;
  }
  if (offer.price_type === "pack") {
    return `${quoted}<br><small>${money(comparable)} por unidad (x${offer.package_quantity})</small>`;
  }
  return quoted;
}

function supplierPriceText(offer) {
  const quoted = money(offer.offer_price);
  if (offer.comparable_unit_price === null || offer.comparable_unit_price === undefined) {
    return `${quoted}, base a revisar`;
  }
  if (offer.price_type === "pack") {
    return `${quoted}, ${money(offer.comparable_unit_price)} por unidad`;
  }
  return quoted;
}

function quantity(value, maximumFractionDigits = 3) {
  if (value === null || value === undefined || value === "") return "";
  return Number(value).toLocaleString("es-AR", {
    maximumFractionDigits,
    minimumFractionDigits: 0,
  });
}

function compactJson(value) {
  return JSON.stringify(value, null, 2);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "Sin datos";
  return new Date(`${value}T00:00:00`).toLocaleDateString("es-AR");
}

function recommendationLabel(value) {
  if (value === "buy") return "Comprar";
  if (value === "review") return "Revisar";
  if (value === "do_not_buy") return "No comprar";
  return value || "";
}

function statusLabel(value) {
  if (value === "pending") return "Pendiente";
  if (value === "accepted") return "Aceptado";
  if (value === "rejected") return "Rechazado";
  return value || "";
}

function matchStatusLabel(candidate) {
  if (candidate.status === "accepted" && candidate.reasons?.includes("auto_accepted_identity_v1")) {
    return "Coincidencia automática";
  }
  return statusLabel(candidate.status);
}

function extractDocumentId(value) {
  const match = value.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
  return match ? match[0] : value.trim();
}

function friendlyError(message) {
  if (message.includes("No se pudo conectar a la base de datos")) {
    return message;
  }
  if (message.includes("failed to resolve host") || message.includes("getaddrinfo failed")) {
    return "No se pudo resolver el host de Supabase. Revisa internet, VPN/DNS o DATABASE_URL.";
  }
  if (message.includes("Internal Server Error")) {
    return "El backend devolvio un error interno. Revisa la terminal del servidor para ver el detalle.";
  }
  if (message.includes("GEMINI_API_KEY")) {
    return "Gemini está suspendido. Usá un método OpenAI disponible.";
  }
  if (message.includes("OPENAI_API_KEY")) {
    return "Falta OPENAI_API_KEY en .env para analizar PDFs/imagenes con OpenAI.";
  }
  if (message.includes("local_text only supports plain text")) {
    return "Texto simple solo acepta TXT/CSV. Para PDF o imagen elegí OpenAI.";
  }
  return message;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail || response.statusText;
    throw new Error(Array.isArray(detail) ? detail.map((item) => item.msg).join("; ") : detail);
  }
  return payload;
}

function alertTemplate(alert) {
  return `
    <div class="alertItem ${escapeHtml(alert.severity)}">
      <strong>${escapeHtml(alert.title)}</strong>
      <span>${escapeHtml(alert.description)}</span>
    </div>
  `;
}

function operationsProductTemplate(product) {
  return `
    <tr>
      <td>${escapeHtml(product.product_name || product.external_product_id || "Sin nombre")}</td>
      <td>${quantity(product.quantity_sold)}</td>
      <td>$${money(product.revenue)}</td>
    </tr>
  `;
}

function assistantProductTemplate(product) {
  return `
    <tr>
      <td>${escapeHtml(product.product_name || product.external_product_id || "Sin nombre")}</td>
      <td>${quantity(product.quantity_sold)}</td>
      <td>${money(product.revenue)}</td>
      <td>${money(product.gross_margin)}</td>
      <td>${escapeHtml(product.sku || "")}</td>
    </tr>
  `;
}

function renderAssistantQuery(data) {
  $("assistantQueryReply").textContent = data.reply || "Sin respuesta.";
  const products = data.metadata?.tool_data?.products || [];
  const message = document.createElement("div");
  message.className = "message assistant";
  const reply = document.createElement("div");
  reply.textContent = data.reply || "Sin respuesta.";
  message.appendChild(reply);
  if (products.length) {
    const table = document.createElement("div");
    table.className = "chatTable";
    table.innerHTML = `<table><thead><tr><th>Producto</th><th>Vendido</th><th>Facturación</th><th>Margen</th><th>SKU</th></tr></thead><tbody>${products.map(assistantProductTemplate).join("")}</tbody></table>`;
    message.appendChild(table);
  }
  $("chatMessages").appendChild(message);
  $("chatMessages").scrollTop = $("chatMessages").scrollHeight;
}

async function runAssistantQuery() {
  const query = $("assistantQueryInput").value.trim();
  if (!query || chatBusy) return;
  chatBusy = true;
  $("assistantQueryForm").querySelector('button[type="submit"]').disabled = true;
  $("assistantQueryInput").value = "";
  $("chatMessages").querySelector(".chatWelcome")?.remove();
  const userMessage = document.createElement("div");
  userMessage.className = "message user";
  userMessage.textContent = query;
  $("chatMessages").appendChild(userMessage);
  const pending = document.createElement("div");
  pending.className = "message assistant";
  pending.textContent = "Pensando...";
  $("chatMessages").appendChild(pending);
  $("chatMessages").scrollTop = $("chatMessages").scrollHeight;
  try {
    const data = await requestJson("/assistant/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        channel: "web",
        external_user_id: "web-user",
        business_id: businessId(),
        message_type: "text",
        text: query,
        raw_payload: { source: "ui.chat" },
      }),
    });
    pending.remove();
    renderAssistantQuery(data);
  } catch (error) {
    pending.classList.add("error");
    pending.textContent = friendlyError(error.message);
  } finally {
    chatBusy = false;
    $("assistantQueryForm").querySelector('button[type="submit"]').disabled = false;
    $("assistantQueryInput").focus();
  }
}

function renderOperationsDashboard(data) {
  const totals = data.sales_totals;
  $("operationsRevenue").textContent = `$${money(totals.revenue)}`;
  $("operationsTickets").textContent = quantity(totals.ticket_count, 0);
  $("operationsUnits").textContent = quantity(totals.quantity_sold);
  $("operationsAverageTicket").textContent = totals.ticket_count
    ? `$${money(Number(totals.revenue) / totals.ticket_count)}` : "—";
  $("homePeriod").textContent = `Últimos 7 días · ${formatDate(data.date_from)} al ${formatDate(data.date_to)}`;
  const lastSale = data.sales_status?.last_sale_date;
  const noRecentSales = !lastSale || lastSale < data.date_from;
  const stale = !lastSale || lastSale < data.date_to;
  $("operationsSalesDetail").textContent = noRecentSales
    ? `No hay ventas cargadas para este período. Última venta: ${formatDate(lastSale)}.`
    : stale
      ? `Ventas cargadas hasta el ${formatDate(lastSale)}; pueden faltar días del período.`
      : `Datos cargados hasta el ${formatDate(lastSale)}. Período: ${formatDate(data.date_from)} al ${formatDate(data.date_to)}.`;
  $("operationsSalesDetail").classList.toggle("isStale", stale);
  $("operationsAlertsCount").textContent = String(data.alerts.length);
  $("operationsAlertsDetail").textContent = data.alerts.length
    ? `${data.alerts.length} alertas activas`
    : "Sin alertas activas.";
  $("operationsRemindersCount").textContent = String(data.pending_reminders.length);
  $("operationsRemindersDetail").textContent =
    `${data.overdue_reminders.length} vencidos.`;
  $("operationsOffersCount").textContent = String(data.supplier_offers.length);
  $("operationsOffersDetail").textContent = "Listas recientes.";
  const actionableAlerts = data.alerts.filter((alert) => alert.severity !== "info");
  $("operationsAlerts").innerHTML = actionableAlerts.length
    ? actionableAlerts.map(alertTemplate).join("")
    : '<div class="alertItem"><strong>Todo al día</strong><span>No hay pendientes urgentes.</span></div>';
  $("operationsTopProductsBody").innerHTML = data.top_products_by_quantity.length
    ? data.top_products_by_quantity.slice(0, 5).map(operationsProductTemplate).join("")
    : '<tr><td colspan="3" class="empty">No hay ventas para mostrar en estos 7 días.</td></tr>';
}

async function loadOperationsDashboard() {
  const { dateFrom, dateTo } = lastSevenDays();
  const params = new URLSearchParams({
    business_id: businessId(),
    date_from: dateFrom,
    date_to: dateTo,
    limit: "5",
    target_coverage_days: "7",
    use_stock: "true",
  });
  const dashboard = await requestJson(`/operations/dashboard?${params.toString()}`);
  renderOperationsDashboard(dashboard);
}

function summarizeAnalysis(data) {
  const report = data.comparison.report;
  const items = data.import_result.items.length;
  const metadata = data.import_result.document.metadata || {};
  const persisted = data.comparison.persisted_count;
  const automatic = report.candidates.filter((item) => item.auto_accepted).length;
  const buy = report.candidates.filter((item) => item.recommendation === "buy").length;
  const review = report.candidates.filter((item) => item.recommendation === "review").length;
  const noBuy = report.candidates.filter((item) => item.recommendation === "do_not_buy").length;
  const seconds = Math.round(Number(metadata.duration_seconds || 0));
  const duration = `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  const summary = metadata.extraction_mode === "dual_independent_chunks"
    ? `${metadata.page_count} páginas · ${metadata.chunk_count} bloques. ` +
      `${items} ofertas encontradas. ` +
      `${metadata.agreed_item_count || 0} con lectura coincidente. ` +
      `${metadata.single_source_item_count || 0} encontradas por un solo lector. ` +
      `${(metadata.retried_pages || []).length} páginas releídas. ` +
      `${metadata.conflict_count || 0} conflictos. Duración: ${duration}. ` +
      ((metadata.needs_review_pages || []).length
        ? `Revisión humana: páginas ${metadata.needs_review_pages.join(", ")}. ` : "")
    : `${items} ofertas encontradas. `;
  const failures = Object.entries(metadata.disabled_providers || {})
    .map(([name, error]) => `${name}: ${error.code || error.error_type}`).join(", ");
  const partial = Object.values(metadata.items_by_page || {}).filter((count) => count === 0).length;
  const warnings = (failures ? `Lectura incompleta (${failures}). ` : "") +
    (partial ? `${partial} páginas sin ofertas recuperadas. ` : "");
  return `${formatDocumentAnalysis(resultAnalysis(data))}. ${summary}${warnings}${report.matched_count} coincidencias con catálogo (${automatic} aceptadas automáticamente), ${buy} compras sugeridas, ${review} precios/ofertas para revisar, ${noBuy} no convienen, ${persisted} sugerencias guardadas.`;
}

function matchCounts(candidates) {
  return {
    total: candidates.length,
    pending: candidates.filter((item) => item.candidate.status === "pending").length,
    accepted: candidates.filter((item) => item.candidate.status === "accepted").length,
    rejected: candidates.filter((item) => item.candidate.status === "rejected").length,
    buy: candidates.filter((item) => item.candidate.recommendation === "buy").length,
  };
}

function filteredMatches() {
  const filter = $("matchStatusFilter").value;
  if (filter === "all") return currentMatches;
  return currentMatches.filter((item) => item.candidate.status === filter);
}

function renderMatches(message = null) {
  const counts = matchCounts(currentMatches);
  const visible = filteredMatches();
  const filterLabel = $("matchStatusFilter").selectedOptions[0].textContent.toLowerCase();
  $("summary").textContent =
    message ||
    `${visible.length} ${filterLabel} - ${counts.pending} pendientes, ${counts.accepted} aceptados, ${counts.rejected} rechazados, ${counts.buy} compras sugeridas`;
  $("matchesBody").innerHTML = visible.length
    ? visible.map(rowTemplate).join("")
    : '<tr><td colspan="8" class="empty">No hay sugerencias para este filtro.</td></tr>';
}

function readerAgreementHtml(offer) {
  const metadata = offer.metadata || {};
  const interpretation = metadata.analysis_v2?.interpretation;
  if (interpretation) {
    const identity = interpretation.identity_status === "confirmed" ? "Producto identificado" :
      interpretation.identity_status === "no_match" ? "Sin coincidencia en el catálogo" : "Producto para revisar";
    const basis = interpretation.price_status === "confirmed" ?
      (interpretation.price_type === "pack" ? "Precio por pack confirmado" : "Precio por unidad confirmado") :
      "Base del precio para revisar";
    const currency = offer.currency === "$" ? " · Moneda por confirmar" : "";
    return `<br><small>${escapeHtml(identity)}<br>${escapeHtml(basis + currency)}</small>`;
  }
  if (!metadata.selected_source && !metadata.agreement_score) return "";
  const source = { gemini: "Gemini", openai: "OpenAI" }[metadata.selected_source] || "lector";
  const labels = {
    agreed_exact: "Coinciden",
    agreed_normalized: `Coinciden, normalizada desde ${source}`,
    agreed_fuzzy: `Coinciden, normalizada desde ${source}`,
    single_source: `Encontrada solo por ${source}`,
    price_conflict: "Conflicto de precio",
    presentation_conflict: "Conflicto de presentación",
    ambiguous: "Requiere revisión",
  };
  const score = metadata.agreement_score == null
    ? "Sin confirmar" : `${Math.round(Number(metadata.agreement_score) * 100)}%`;
  return `<br><small>${metadata.agreement_status === "single_source" ? `Lectura: ${escapeHtml(source)}` : `Coincidencia Gemini/OpenAI: ${score}`}<br>Estado: ${escapeHtml(labels[metadata.agreement_status] || "Requiere revisión")}</small>`;
}

function rowTemplate(item) {
  const candidate = item.candidate;
  const offer = item.supplier_offer_item;
  const product = item.product;
  return `
    <tr>
      <td>${matchStatusLabel(candidate)}<br>${recommendationLabel(candidate.recommendation)}</td>
      <td>${escapeHtml(offer.raw_name)}${readerAgreementHtml(offer)}</td>
      <td>${supplierPriceHtml(offer)}</td>
      <td>${product ? escapeHtml(product.name) : "Sin match"}</td>
      <td>${product ? money(product.current_cost) : ""}</td>
      <td>${money(candidate.cost_difference)}</td>
      <td>${money(candidate.confidence_score)}</td>
      <td>
        <div class="actions">
          <button data-action="accept" data-id="${candidate.id}" ${candidate.status === "accepted" ? "disabled" : ""}>Aceptar</button>
          <button class="danger" data-action="reject" data-id="${candidate.id}" ${candidate.status === "rejected" ? "disabled" : ""}>Rechazar</button>
          <button class="secondary" data-action="correct" data-id="${candidate.id}">Corregir</button>
        </div>
      </td>
    </tr>
  `;
}

function productSearchTemplate(product) {
  return `
    <tr>
      <td>${escapeHtml(product.name)}</td>
      <td>${escapeHtml(product.sku || "")}</td>
      <td>${escapeHtml(product.barcode || "")}</td>
      <td>${money(product.current_cost)}</td>
      <td>${money(product.sale_price)}</td>
      <td>
        <button data-action="select-product" data-id="${escapeHtml(product.id)}" type="button">Elegir</button>
      </td>
    </tr>
  `;
}

async function runProductSearch() {
  const query = $("productSearchInput").value.trim();
  if (!query) {
    $("productSearchSummary").textContent = "Escribí al menos una parte del nombre, SKU o código.";
    $("productSearchBody").innerHTML =
      '<tr><td colspan="6" class="empty">Sin búsqueda.</td></tr>';
    return;
  }
  $("productSearchSummary").textContent = "Buscando productos...";
  $("productSearchBody").innerHTML =
    '<tr><td colspan="6" class="empty">Buscando productos...</td></tr>';
  const params = new URLSearchParams({
    business_id: businessId(),
    q: query,
    limit: "30",
  });
  const products = await requestJson(`/procurement/products/search?${params.toString()}`);
  $("productSearchSummary").textContent = `${products.length} productos encontrados`;
  $("productSearchBody").innerHTML = products.length
    ? products.map(productSearchTemplate).join("")
    : '<tr><td colspan="6" class="empty">No se encontraron productos.</td></tr>';
}

function openProductSearch(candidateId) {
  correctionCandidateId = candidateId;
  const match = currentMatches.find((item) => item.candidate.id === candidateId);
  if (!match) return;
  $("productSearchContext").textContent =
    `Oferta: ${match.supplier_offer_item.raw_name} - costo proveedor ${supplierPriceText(match.supplier_offer_item)}. ` +
    `Match actual: ${match.product ? match.product.name : "sin match"}.`;
  $("productSearchInput").value = match.supplier_offer_item.raw_name;
  $("productSearchSummary").textContent = "Buscá y elegí el producto correcto.";
  $("productSearchBody").innerHTML =
    '<tr><td colspan="6" class="empty">Tocá Buscar para ver resultados.</td></tr>';
  $("productSearchDialog").showModal();
}

async function correctMatchWithProduct(productId) {
  if (!correctionCandidateId) return;
  $("productSearchSummary").textContent = "Guardando corrección...";
  const feedback = await requestJson(
    `/procurement/product-matches/${correctionCandidateId}/correct`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        business_id: businessId(),
        product_id: productId,
        reviewed_by_user_id: "web-user",
        notes: "Corregido desde buscador de catalogo",
      }),
    },
  );
  $("productSearchDialog").close();
  correctionCandidateId = "";
  await loadMatches();
  renderMatches(feedback.accepted ? "Corrección guardada y recomendación recalculada." : "Corrección guardada.");
  await loadDocuments();
  if (currentDocumentId) {
    await loadDocumentPurchasePlan({ scroll: false });
  }
}

function filteredPurchasePlanItems(items) {
  const filter = $("purchasePlanFilter").value;
  if (filter === "with_sales") {
    return items.filter((item) => item.sales_summary && Number(item.sales_summary.quantity_sold) > 0);
  }
  if (filter === "without_sales") {
    return items.filter((item) => !item.sales_summary || Number(item.sales_summary.quantity_sold) <= 0);
  }
  if (filter === "suggested") {
    return items.filter((item) => item.suggested_quantity !== null && Number(item.suggested_quantity) > 0);
  }
  if (filter === "positive_savings") {
    return items.filter((item) => Number(item.candidate.cost_difference || 0) > 0);
  }
  if (filter === "buy" || filter === "review") {
    return items.filter((item) => item.candidate.recommendation === filter);
  }
  if (filter === "no_match") {
    return items.filter((item) => !item.product);
  }
  return items;
}

function renderPurchasePlan(data) {
  currentPurchasePlan = data;
  renderFilteredPurchasePlan();
}

function renderFilteredPurchasePlan() {
  const data = currentPurchasePlan;
  if (!data) return;
  const visible = filteredPurchasePlanItems(data.items);
  const coverageText =
    data.target_coverage_days === null
      ? "cantidad sugerida sin calcular"
      : `${data.target_coverage_days} dias a cubrir`;
  const selected = documentSummaries.find((item) => item.document.id === currentDocumentId);
  const documentText = selected
    ? `${selected.supplier_name || "Proveedor"} - ${selected.document.source_filename}`
    : "documento seleccionado";
  $("purchasePlanSummary").textContent =
    `${documentText}: ${visible.length}/${data.items.length} items - ventas ${data.date_from} a ${data.date_to} - ${coverageText} - orden ${sortLabel(data.sort_by)}`;
  $("purchasePlanBody").innerHTML = visible.length
    ? visible.map(purchasePlanTemplate).join("")
    : '<tr><td colspan="6" class="empty">No hay ofertas para este filtro.</td></tr>';
}

function sortLabel(value) {
  if (value === "sales_then_savings") return "mas vendidos + ahorro";
  if (value === "estimated_savings") return "ahorro estimado";
  if (value === "savings_then_sales") return "mayor ahorro unitario";
  if (value === "supplier_order") return "proveedor";
  return value || "";
}

function purchasePlanTemplate(item) {
  const summary = item.sales_summary;
  const difference = item.candidate.cost_difference;
  return `
    <tr>
      <td>${escapeHtml(item.supplier_offer_item.raw_name)}${readerAgreementHtml(item.supplier_offer_item)}</td>
      <td>${supplierPriceHtml(item.supplier_offer_item)}</td>
      <td>${item.product ? escapeHtml(item.product.name) : "Sin coincidencia"}${summary ? `<br><small>${quantity(summary.quantity_sold)} vendidos · ${quantity(summary.average_daily_quantity, 2)}/día</small>` : ""}</td>
      <td>${item.product?.current_cost == null ? "—" : `$${money(item.product.current_cost)}`}</td>
      <td>${difference == null ? "—" : `$${money(difference)}`}${item.suggested_quantity == null ? "" : `<br><small>Sugerido: ${quantity(item.suggested_quantity)}</small>`}</td>
      <td>${matchStatusLabel(item.candidate)}<br>${recommendationLabel(item.candidate.recommendation)}</td>
    </tr>
  `;
}

function documentTemplate(summary) {
  const document = summary.document;
  const createdAt = new Date(document.created_at).toLocaleString("es-AR");
  const isOpen = document.id === currentDocumentId;
  return `
    <tr class="${isOpen ? "selectedRow" : ""}">
      <td><strong>${escapeHtml(summary.supplier_name || "Sin proveedor")}</strong><br><small>${escapeHtml(document.source_filename)}<br>${escapeHtml(formatDocumentAnalysis(summary.analysis || document.metadata?.analysis))}</small></td>
      <td>${createdAt}</td>
      <td>${summary.item_count}</td>
      <td>${summary.matched_count}/${summary.candidate_count}</td>
      <td>${summary.pending_count} coincidencias<br>${escapeHtml((summary.analysis || document.metadata?.analysis)?.pending_block_count ?? "—")} bloques</td>
      <td>
        <button class="buttonSecondary" data-action="open-document" data-id="${escapeHtml(document.id)}">Ver detalle</button>
      </td>
    </tr>
  `;
}

function documentOptionTemplate(summary) {
  const document = summary.document;
  const createdAt = new Date(document.created_at).toLocaleString("es-AR");
  const supplier = summary.supplier_name || "Sin proveedor";
  return `<option value="${escapeHtml(document.id)}">${escapeHtml(supplier)} - ${escapeHtml(document.source_filename)} - ${createdAt}</option>`;
}

function renderDocuments(data) {
  documentSummaries = data;
  const selected = data.find((item) => item.document.id === currentDocumentId);
  if (selected) {
    $("detailTitle").textContent = selected.supplier_name || "Detalle del catálogo";
    $("detailSubtitle").textContent =
      `${selected.document.source_filename} · ${new Date(selected.document.created_at).toLocaleDateString("es-AR")} · ${formatDocumentAnalysis(documentAnalyses.get(`${businessId()}:${currentDocumentId}`)?.analysis || selected.analysis || selected.document.metadata?.analysis)}`;
  }
  $("documentsCount").textContent = `${data.length} documentos`;
  $("documentsStatusDetail").textContent = data.length
    ? "Elegí una lista para ver el plan de compra."
    : "Cargá una lista para comparar precios.";
  $("documentsSummary").textContent = `${data.length} listas analizadas`;
  $("documentsBody").innerHTML = data.length
    ? data.map(documentTemplate).join("")
    : '<tr><td colspan="6" class="empty">Todavía no hay comparaciones. Analizá un PDF para empezar.</td></tr>';
  $("homeRecentDocuments").innerHTML = data.length
    ? data.slice(0, 3).map((summary) => `
      <div class="recentItem">
        <div><strong>${escapeHtml(summary.supplier_name || "Proveedor")}</strong><small>${new Date(summary.document.created_at).toLocaleDateString("es-AR")} · ${summary.item_count} ofertas</small></div>
        <a href="#detalle/${encodeURIComponent(summary.document.id)}" aria-label="Ver análisis de ${escapeHtml(summary.supplier_name || "proveedor")}">↗</a>
      </div>`).join("")
    : '<p class="muted">Todavía no hay catálogos analizados.</p>';
  $("documentSelector").innerHTML =
    '<option value="">Elegí una lista de proveedor</option>' + data.map(documentOptionTemplate).join("");
  if (currentDocumentId) {
    $("documentSelector").value = currentDocumentId;
  }
}

function renderSalesStatus(data) {
  if (!data.last_sale_date) {
    setStatus("salesStatus", "Sin ventas");
    $("lastSaleDate").textContent = "Sin ventas";
    $("lastSalesSync").textContent = "Sin datos";
    $("salesStatusDetail").textContent = "Cargá ventas para calcular demanda.";
    $("salesLinesDetail").textContent = "0 líneas guardadas";
    return;
  }

  const lastSaleDate = formatDate(data.last_sale_date);
  const firstSaleDate = formatDate(data.first_sale_date);
  const importedAt = data.last_imported_at
    ? new Date(data.last_imported_at).toLocaleString("es-AR")
    : "sin fecha";

  setStatus("salesStatus", `Hasta ${lastSaleDate}`, "ok");
  $("lastSaleDate").textContent = lastSaleDate;
  $("lastSalesSync").textContent = importedAt;
  $("salesStatusDetail").textContent = `Ventas cargadas desde ${firstSaleDate}`;
  $("salesLinesDetail").textContent = `${data.total_sale_lines} líneas guardadas`;
}

async function loadSalesStatus() {
  const data = await requestJson(`/sales/status?business_id=${encodeURIComponent(businessId())}`);
  renderSalesStatus(data);
}

async function loadCatalogStatus() {
  const data = await requestJson(
    `/procurement/catalog-imports/status?business_id=${encodeURIComponent(businessId())}`,
  );
  const lastImport = data.last_import;
  if (!lastImport) {
    setStatus("catalogStatus", "Sin cargas");
    $("catalogStatusSummary").textContent = "Sin productos";
    $("catalogSyncDetail").textContent = "Todavía no hay una carga completa del catálogo POS.";
    return;
  }
  const importedAt = new Date(lastImport.completed_at).toLocaleString("es-AR");
  const counts = lastImport.summary || {};
  setStatus("catalogStatus", "Sincronizado", "ok");
  $("catalogStatusSummary").textContent = `${quantity(data.total_products, 0)} productos`;
  $("catalogSyncDetail").textContent =
    `Última carga: ${importedAt} · Archivo: ${lastImport.source_filename} · ` +
    `${data.total_products} productos guardados · ` +
    `${counts.created_count ?? "—"} nuevos, ${counts.updated_count ?? "—"} modificados, ` +
    `${counts.unchanged_count ?? "—"} sin cambios` +
    (counts.unresolved_family_count
      ? ` · ${counts.unresolved_family_count} sin familia resuelta: cargá también el CSV de familias.`
      : "");
}

async function loadDocuments() {
  $("documentsSummary").textContent = "Cargando listas analizadas...";
  $("documentsBody").innerHTML =
    '<tr><td colspan="6" class="empty">Cargando listas analizadas...</td></tr>';
  const data = await requestJson(
    `/procurement/supplier-offers/summaries?business_id=${encodeURIComponent(businessId())}&limit=100`,
  );
  renderDocuments(data);
}

async function loadMatches() {
  const documentId = extractDocumentId($("documentId").value || $("documentSelector").value);
  if (!documentId) return;
  currentDocumentId = documentId;
  $("documentId").value = documentId;
  $("documentSelector").value = documentId;
  $("summary").textContent = "Cargando matches...";
  $("matchesBody").innerHTML = '<tr><td colspan="8" class="empty">Cargando matches...</td></tr>';
  const data = await requestJson(
    `/procurement/supplier-offers/${encodeURIComponent(documentId)}/matches?business_id=${encodeURIComponent(businessId())}`,
  );
  currentMatches = data.candidates;
  renderMatches();
}

async function loadDocumentPurchasePlan({ scroll = true } = {}) {
  const documentId = extractDocumentId($("documentSelector").value || $("documentId").value);
  if (!documentId) return;
  currentDocumentId = documentId;
  $("documentId").value = documentId;
  $("documentSelector").value = documentId;
  const params = buildPurchasePlanParams();
  $("purchasePlanSummary").textContent = "Calculando plan de compra...";
  $("purchasePlanBody").innerHTML =
    '<tr><td colspan="6" class="empty">Calculando resultados...</td></tr>';
  const data = await requestJson(
    `/procurement/supplier-offers/${encodeURIComponent(documentId)}/purchase-plan?${params.toString()}`,
  );
  renderPurchasePlan(data);
  if (scroll) {
    $("purchasePlanPanel").scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function buildPurchasePlanParams() {
  const params = new URLSearchParams({
    business_id: businessId(),
    date_from: $("salesDateFrom").value,
    date_to: $("salesDateTo").value,
    use_stock: $("useStock").checked ? "true" : "false",
    ignore_negative_stock: "true",
    sort_by: $("purchasePlanSort").value,
    only_positive_savings: $("onlyPositiveSavings").checked ? "true" : "false",
  });
  const coverageDays = $("targetCoverageDays").value.trim();
  if (coverageDays) {
    params.set("target_coverage_days", coverageDays);
  }
  return params;
}

async function downloadPurchasePlanPdf() {
  const documentId = extractDocumentId($("documentSelector").value || $("documentId").value);
  if (!documentId) {
    $("purchasePlanSummary").textContent = "Elegi una lista analizada para descargar el PDF.";
    return;
  }
  const params = buildPurchasePlanParams();
  const response = await fetch(
    `/procurement/supplier-offers/${encodeURIComponent(documentId)}/purchase-plan.pdf?${params.toString()}`,
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || response.statusText);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `plan-compra-${documentId}.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function openDocument(documentId) {
  currentDocumentId = documentId;
  $("documentId").value = documentId;
  $("documentSelector").value = documentId;
  const selected = documentSummaries.find((item) => item.document.id === documentId);
  $("detailTitle").textContent = selected ? selected.supplier_name || "Detalle del catálogo" : "Detalle del catálogo";
  $("detailSubtitle").textContent = selected
    ? `${selected.document.source_filename} · ${new Date(selected.document.created_at).toLocaleDateString("es-AR")} · ${formatDocumentAnalysis(selected.analysis || selected.document.metadata?.analysis)}`
    : "Método no registrado. Precios, ventas y coincidencias del proveedor.";
  if (location.hash !== `#detalle/${documentId}`) location.hash = `detalle/${documentId}`;
  showView("detalle");
  await loadDocumentAnalysis(documentId);
  await loadMatches();
  await loadDocumentPurchasePlan();
  await loadDocuments();
}

async function analyzePdf(file, supplierName, operation) {
  if (!file || !file.name.toLowerCase().endsWith(".pdf")) {
    throw new Error("Elegí un archivo PDF para cada proveedor.");
  }
  const form = new FormData();
  form.append("business_id", operation.businessId);
  form.append("supplier_name", supplierName.trim());
  form.append("extraction_provider", "auto");
  form.append("analysis_method", operation.method);
  for (const [key, value] of Object.entries(operation.thresholds)) form.append(key, String(value));
  form.append("persist_candidates", "true");
  form.append("file", file);
  const data = await requestJson("/procurement/supplier-offers/from-document", {
    method: "POST",
    body: form,
  });
  documentAnalyses.set(`${operation.businessId}:${data.import_result.document.id}`, { analysis: resultAnalysis(data), pending_blocks: data.pending_blocks || [] });
  return data;
}

function bestOffersByProduct(analysis) {
  const offers = new Map();
  let needsReview = 0;
  for (const candidate of analysis.comparison.report.candidates) {
    const product = candidate.product;
    const offer = candidate.supplier_offer_item;
    const price = Number(offer.comparable_unit_price);
    if (!product?.id || !Number.isFinite(price) || price <= 0 || offer.currency !== "ARS") {
      needsReview += 1;
      continue;
    }
    const previous = offers.get(product.id);
    if (!previous || price < previous.price) {
      offers.set(product.id, {
        product,
        offer,
        price,
        confirmed: candidate.auto_accepted === true,
      });
    }
    if (!candidate.auto_accepted) needsReview += 1;
  }
  return { offers, needsReview };
}

function dualPriceCell(item) {
  if (!item) return '<span class="muted">Sin precio comparable</span>';
  const pack = item.offer.price_type === "pack" ? "<br><small>Precio por unidad del pack</small>" : "";
  return `<strong>$${money(item.price)}</strong>${pack}<br><small>${escapeHtml(item.offer.raw_name)}</small>`;
}

function renderDualComparison(first, second, nameA, nameB) {
  const a = bestOffersByProduct(first);
  const b = bestOffersByProduct(second);
  const productIds = [...new Set([...a.offers.keys(), ...b.offers.keys()])];
  const rows = productIds.map((id) => {
    const left = a.offers.get(id);
    const right = b.offers.get(id);
    const product = left?.product || right?.product;
    let verdict = "Revisar";
    if (left && right) {
      const sameTaxBasis = left.offer.tax_included === right.offer.tax_included;
      if (sameTaxBasis && left.confirmed && right.confirmed) {
        verdict = left.price === right.price ? "Igual precio" : left.price < right.price ? nameA : nameB;
      }
    } else {
      verdict = left ? `Precio comparable solo en ${nameA}` : `Precio comparable solo en ${nameB}`;
    }
    return { product, left, right, verdict };
  });
  rows.sort((left, right) => {
    const bothLeft = Boolean(left.left && left.right);
    const bothRight = Boolean(right.left && right.right);
    return Number(bothRight) - Number(bothLeft) ||
      left.product.name.localeCompare(right.product.name, "es");
  });
  const shared = rows.filter((row) => row.left && row.right).length;
  const reviewCount = a.needsReview + b.needsReview;
  $("dualHeaderA").textContent = nameA;
  $("dualHeaderB").textContent = nameB;
  $("dualResultSummary").innerHTML =
    `<span>${shared} productos en ambos catálogos · ${rows.length} productos con precio comparable</span>` +
    `<span>${escapeHtml(nameA)}: ${escapeHtml(formatDocumentAnalysis(resultAnalysis(first)))}</span>` +
    `<span>${escapeHtml(nameB)}: ${escapeHtml(formatDocumentAnalysis(resultAnalysis(second)))}</span>`;
  $("dualResultBody").innerHTML = rows.length
    ? rows.map((row) => `<tr>
        <td><strong>${escapeHtml(row.product.name)}</strong><br><small>${escapeHtml(row.product.sku || "")}</small></td>
        <td>${dualPriceCell(row.left)}</td>
        <td>${dualPriceCell(row.right)}</td>
        <td>${row.product.current_cost == null ? "Sin costo" : `$${money(row.product.current_cost)}`}</td>
        <td>${escapeHtml(row.verdict)}</td>
      </tr>`).join("")
    : '<tr><td colspan="5" class="empty">No hay productos vinculados para comparar. Revisá las coincidencias de cada archivo.</td></tr>';
  const idA = first.import_result.document.id;
  const idB = second.import_result.document.id;
  $("dualUnmatched").innerHTML =
    `La tabla incluye solo precios comparables. Consultá también las ofertas sin vínculo y pendientes en cada detalle. <a href="#detalle/${encodeURIComponent(idA)}">Ver ${escapeHtml(nameA)}</a> · <a href="#detalle/${encodeURIComponent(idB)}">Ver ${escapeHtml(nameB)}</a>` +
    `<h3>${escapeHtml(nameA)}: ofertas que necesitan datos</h3>${pendingBlocksHtml(first.pending_blocks || [])}` +
    `<h3>${escapeHtml(nameB)}: ofertas que necesitan datos</h3>${pendingBlocksHtml(second.pending_blocks || [])}`;
  $("dualResult").hidden = false;
  $("dualResultMessage").textContent = analysisNeedsReview(first) || analysisNeedsReview(second)
    ? "Catálogos guardados con pendientes. Revisá los datos de cada proveedor."
    : "Catálogos guardados. El menor precio se destaca solo con identidad confirmada y la misma base de impuestos.";
  $("dualResultMessage").className = "formFeedback success";
}

$("catalogForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("catalogStatus", "Cargando");
  const form = new FormData();
  form.append("business_id", businessId());
  form.append("file", $("catalogFile").files[0]);
  const familiesFile = $("catalogFamiliesFile").files[0];
  if (familiesFile) form.append("families_file", familiesFile);
  try {
    const data = await requestJson("/procurement/catalog-imports/from-file", {
      method: "POST",
      body: form,
    });
    setStatus("catalogStatus", "Listo", "ok");
    $("catalogResult").textContent = compactJson({
      id: data.id,
      status: data.status,
      row_count: data.row_count,
      imported_count: data.imported_count,
      summary: data.summary,
    });
    loadCatalogStatus().catch((error) => {
      $("catalogSyncDetail").textContent = friendlyError(error.message);
    });
  } catch (error) {
    setStatus("catalogStatus", "Error", "error");
    $("catalogResult").textContent = friendlyError(error.message);
  }
});

async function submitAnalysis(event, dual) {
  event.preventDefault();
  if (analysisBusy) return;
  const status = dual ? "dualStatus" : "documentStatus";
  const result = $(dual ? "dualResultMessage" : "documentResult");
  let first = null;
  let operation;
  try {
    operation = captureAnalysisOperation(dual);
  } catch (error) {
    setStatus(status, "Revisar", "error");
    result.textContent = error.message;
    return;
  }
  setAnalysisBusy(true);
  result.className = "formFeedback";
  if (dual) $("dualResult").hidden = true;
  try {
    setStatus(status, dual ? "Analizando 1 de 2" : "Analizando");
    result.textContent = "Leyendo el PDF. Puede tardar unos minutos…";
    first = await analyzePdf(operation.uploads[0].file, operation.uploads[0].name, operation);
    if (dual) {
      setStatus(status, "Analizando 2 de 2");
      result.textContent = "Primer catálogo guardado. Analizando el segundo…";
      const second = await analyzePdf(operation.uploads[1].file, operation.uploads[1].name, operation);
      renderDualComparison(first, second, operation.uploads[0].name, operation.uploads[1].name);
      const partial = [first, second].some((data) => resultAnalysis(data)?.status === "partial");
      const review = [first, second].some(analysisNeedsReview);
      setStatus(status, partial ? "Parcial" : review ? "Guardado / Revisar" : "Guardado", review ? "" : "ok");
    } else {
      result.textContent = summarizeAnalysis(first);
      const link = document.createElement("a");
      link.href = `#detalle/${encodeURIComponent(first.import_result.document.id)}`;
      link.textContent = " Ver detalle guardado";
      result.appendChild(link);
      const partial = resultAnalysis(first)?.status === "partial";
      const review = analysisNeedsReview(first);
      setStatus(status, partial ? "Parcial" : review ? "Guardado / Revisar" : "Guardado", review ? "" : "ok");
      result.className = "formFeedback success";
    }
    loadDocuments().catch(() => { $("documentsSummary").textContent = "Análisis guardado. No pudimos actualizar el historial."; });
  } catch (error) {
    setStatus(status, first ? "Primero guardado / Error" : "Error", "error");
    result.textContent = friendlyError(error.message);
    if (first) {
      const link = document.createElement("a");
      link.href = `#detalle/${encodeURIComponent(first.import_result.document.id)}`;
      link.textContent = " Abrir primer catálogo guardado";
      result.appendChild(link);
    }
    result.className = "formFeedback error";
  } finally {
    setAnalysisBusy(false);
    if (businessId() !== operation.businessId) loadAnalysisMethods();
  }
}

$("documentForm").addEventListener("submit", (event) => submitAnalysis(event, false));
$("dualDocumentForm").addEventListener("submit", (event) => submitAnalysis(event, true));
$("analysisMethod").addEventListener("change", refreshAnalysisAvailability);
$("retryAnalysisMethods").addEventListener("click", loadAnalysisMethods);
$("retryDocumentAnalysis").addEventListener("click", () => {
  if (currentDocumentId) loadDocumentAnalysis(currentDocumentId);
});

$("salesForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("salesStatus", "Cargando");
  $("salesResult").textContent = "Importando ventas...";
  const form = new FormData();
  form.append("business_id", businessId());
  form.append("file", $("salesFile").files[0]);
  try {
    const data = await requestJson("/sales/imports/from-file", {
      method: "POST",
      body: form,
    });
    $("salesResult").textContent =
      `${data.imported_count} lineas nuevas, ${data.summary.duplicate_line_count} duplicadas, ` +
      `${data.unique_product_count} productos vendidos, ${data.unique_ticket_count} tickets.`;
    await loadSalesStatus();
    if (data.period_start && data.period_end) {
      $("salesDateFrom").value = data.period_start;
      $("salesDateTo").value = data.period_end;
    }
    if (currentDocumentId) {
      await loadDocumentPurchasePlan({ scroll: false });
    }
  } catch (error) {
    setStatus("salesStatus", "Error", "error");
    $("salesResult").textContent = friendlyError(error.message);
  }
});

$("assistantQueryForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await runAssistantQuery();
});

$("chatMessages").addEventListener("click", (event) => {
  const button = event.target.closest("[data-chat-suggestion]");
  if (!button) return;
  $("assistantQueryInput").value = button.dataset.chatSuggestion;
  $("assistantQueryForm").requestSubmit();
});

$("assistantQueryInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("assistantQueryForm").requestSubmit();
  }
});

document.querySelectorAll("[data-mode]").forEach((button) => {
  button.addEventListener("click", () => setComparisonMode(button.dataset.mode));
});

$("backToComparisons").addEventListener("click", () => {
  location.hash = "comparaciones";
});

window.addEventListener("hashchange", routeFromHash);

$("loadDocuments").addEventListener("click", async () => {
  $("loadDocuments").disabled = true;
  try {
    await loadDocuments();
  } catch (error) {
    $("documentsSummary").textContent = friendlyError(error.message);
  } finally {
    $("loadDocuments").disabled = false;
  }
});

$("documentSelector").addEventListener("change", async () => {
  const documentId = $("documentSelector").value;
  if (!documentId) return;
  try {
    await openDocument(documentId);
  } catch (error) {
    $("purchasePlanSummary").textContent = friendlyError(error.message);
  }
});

$("loadMatches").addEventListener("click", async () => {
  $("loadMatches").disabled = true;
  try {
    await loadMatches();
  } catch (error) {
    $("summary").textContent = friendlyError(error.message);
  } finally {
    $("loadMatches").disabled = false;
  }
});

$("loadPurchasePlan").addEventListener("click", async () => {
  $("loadPurchasePlan").disabled = true;
  try {
    await loadDocumentPurchasePlan();
  } catch (error) {
    $("purchasePlanSummary").textContent = friendlyError(error.message);
  } finally {
    $("loadPurchasePlan").disabled = false;
  }
});

$("matchStatusFilter").addEventListener("change", () => {
  renderMatches();
});

$("purchasePlanFilter").addEventListener("change", () => {
  renderFilteredPurchasePlan();
});

$("purchasePlanSort").addEventListener("change", async () => {
  await loadDocumentPurchasePlan({ scroll: false });
});

$("onlyPositiveSavings").addEventListener("change", async () => {
  await loadDocumentPurchasePlan({ scroll: false });
});

$("printPurchasePlan").addEventListener("click", async () => {
  $("printPurchasePlan").disabled = true;
  try {
    await downloadPurchasePlanPdf();
  } catch (error) {
    $("purchasePlanSummary").textContent = friendlyError(error.message);
  } finally {
    $("printPurchasePlan").disabled = false;
  }
});

$("loadOperationsDashboard").addEventListener("click", async () => {
  $("loadOperationsDashboard").disabled = true;
  try {
    await loadOperationsDashboard();
  } catch (error) {
    $("dailySummaryText").textContent = friendlyError(error.message);
  } finally {
    $("loadOperationsDashboard").disabled = false;
  }
});

$("documentsBody").addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const action = button.dataset.action;
  const id = button.dataset.id;
  if (action !== "open-document") return;
  button.disabled = true;
  try {
    await openDocument(id);
  } catch (error) {
    $("purchasePlanSummary").textContent = friendlyError(error.message);
  } finally {
    button.disabled = false;
  }
});

$("matchesBody").addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const action = button.dataset.action;
  const id = button.dataset.id;
  button.disabled = true;
  try {
    let feedback = null;
    if (action === "correct") {
      openProductSearch(id);
      return;
    } else {
      feedback = await requestJson(`/procurement/product-matches/${id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          business_id: businessId(),
          reviewed_by_user_id: "web-user",
          notes: action === "accept" ? "Aceptado desde mini frontend" : "Rechazado desde mini frontend",
        }),
      });
    }
    currentMatches = currentMatches.map((item) => {
      if (item.candidate.id !== id) return item;
      return {
        ...item,
        candidate: {
          ...item.candidate,
          status: feedback.accepted ? "accepted" : "rejected",
          product_id: feedback.candidate_product_id,
          relationship_type: feedback.relationship_type,
        },
      };
    });
    renderMatches(feedback.accepted ? "Guardado: aceptado." : "Guardado: rechazado.");
    await loadDocuments();
    if (currentDocumentId) {
      await loadDocumentPurchasePlan({ scroll: false });
    }
  } catch (error) {
    $("summary").textContent = friendlyError(error.message);
  } finally {
    button.disabled = false;
  }
});

$("runProductSearch").addEventListener("click", async () => {
  try {
    await runProductSearch();
  } catch (error) {
    $("productSearchSummary").textContent = friendlyError(error.message);
  }
});

$("productSearchInput").addEventListener("keydown", async (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  try {
    await runProductSearch();
  } catch (error) {
    $("productSearchSummary").textContent = friendlyError(error.message);
  }
});

$("closeProductSearch").addEventListener("click", () => {
  $("productSearchDialog").close();
  correctionCandidateId = "";
});

$("productSearchBody").addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button || button.dataset.action !== "select-product") return;
  button.disabled = true;
  try {
    await correctMatchWithProduct(button.dataset.id);
  } catch (error) {
    $("productSearchSummary").textContent = friendlyError(error.message);
  } finally {
    button.disabled = false;
  }
});

loadDocuments().catch((error) => {
  $("documentsSummary").textContent = friendlyError(error.message);
});

loadSalesStatus().catch((error) => {
  $("salesStatusDetail").textContent = friendlyError(error.message);
});

loadCatalogStatus().catch((error) => {
  $("catalogSyncDetail").textContent = friendlyError(error.message);
});

$("businessId").addEventListener("change", () => {
  if (analysisBusy) return;
  loadAnalysisMethods();
  currentDocumentId = "";
  currentMatches = [];
  currentPurchasePlan = null;
  location.hash = "inicio";
  loadDocuments().catch((error) => {
    $("documentsSummary").textContent = friendlyError(error.message);
  });
  loadSalesStatus().catch((error) => {
    $("salesStatusDetail").textContent = friendlyError(error.message);
  });
  loadCatalogStatus().catch((error) => {
    $("catalogSyncDetail").textContent = friendlyError(error.message);
  });
  loadOperationsDashboard().catch((error) => {
    $("operationsSalesDetail").textContent = friendlyError(error.message);
  });
});

loadOperationsDashboard().catch((error) => {
  $("operationsSalesDetail").textContent = friendlyError(error.message);
});

const initialPeriod = lastSevenDays();
$("salesDateFrom").value = initialPeriod.dateFrom;
$("salesDateTo").value = initialPeriod.dateTo;
routeFromHash();

loadAnalysisMethods();
