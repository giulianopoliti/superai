const $ = (id) => document.getElementById(id);
let currentMatches = [];
let currentDocumentId = "";

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

function extractDocumentId(value) {
  const match = value.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
  return match ? match[0] : value.trim();
}

function friendlyError(message) {
  if (message.includes("GEMINI_API_KEY")) {
    return "Falta GEMINI_API_KEY en .env para analizar PDFs/imagenes con Gemini.";
  }
  if (message.includes("OPENAI_API_KEY")) {
    return "Falta OPENAI_API_KEY en .env para analizar PDFs/imagenes con OpenAI.";
  }
  if (message.includes("local_text only supports plain text")) {
    return "Texto simple solo acepta TXT/CSV. Para PDF o imagen elegi Gemini u OpenAI.";
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

function summarizeAnalysis(data) {
  const report = data.comparison.report;
  const items = data.import_result.items.length;
  const persisted = data.comparison.persisted_count;
  const buy = report.candidates.filter((item) => item.recommendation === "buy").length;
  const review = report.candidates.filter((item) => item.recommendation === "review").length;
  const noBuy = report.candidates.filter((item) => item.recommendation === "do_not_buy").length;
  return `Documento ${data.import_result.document.id} - items ${items}, matches ${report.matched_count}, comprar ${buy}, revisar ${review}, no comprar ${noBuy}, guardados ${persisted}`;
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

function rowTemplate(item) {
  const candidate = item.candidate;
  const offer = item.supplier_offer_item;
  const product = item.product;
  return `
    <tr>
      <td>${escapeHtml(candidate.status)}<br>${escapeHtml(candidate.recommendation)}</td>
      <td>${escapeHtml(offer.raw_name)}</td>
      <td>${money(offer.offer_price)}</td>
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

function documentTemplate(summary) {
  const document = summary.document;
  const createdAt = new Date(document.created_at).toLocaleString("es-AR");
  const isOpen = document.id === currentDocumentId;
  return `
    <tr class="${isOpen ? "selectedRow" : ""}">
      <td>${escapeHtml(summary.supplier_name || "Sin proveedor")}</td>
      <td>${escapeHtml(document.source_filename)}</td>
      <td>${createdAt}</td>
      <td>${summary.item_count}</td>
      <td>${summary.matched_count}/${summary.candidate_count}</td>
      <td>${summary.buy_count}</td>
      <td>
        ${summary.pending_count} pendientes<br>
        ${summary.accepted_count} aceptados / ${summary.rejected_count} rechazados
      </td>
      <td>
        <button data-action="open-document" data-id="${escapeHtml(document.id)}">Abrir</button>
      </td>
    </tr>
  `;
}

async function loadMatches() {
  const documentId = extractDocumentId($("documentId").value);
  if (!documentId) return;
  currentDocumentId = documentId;
  $("documentId").value = documentId;
  $("summary").textContent = "Cargando sugerencias...";
  $("matchesBody").innerHTML = '<tr><td colspan="8" class="empty">Cargando sugerencias...</td></tr>';
  const data = await requestJson(
    `/procurement/supplier-offers/${encodeURIComponent(documentId)}/matches?business_id=${encodeURIComponent(businessId())}`,
  );
  currentMatches = data.candidates;
  renderMatches();
}

async function loadDocuments() {
  $("documentsSummary").textContent = "Cargando documentos analizados...";
  $("documentsBody").innerHTML =
    '<tr><td colspan="8" class="empty">Cargando documentos analizados...</td></tr>';
  const data = await requestJson(
    `/procurement/supplier-offers/summaries?business_id=${encodeURIComponent(businessId())}&limit=20`,
  );
  $("documentsSummary").textContent = `${data.length} documentos analizados`;
  $("documentsBody").innerHTML = data.length
    ? data.map(documentTemplate).join("")
    : '<tr><td colspan="8" class="empty">Todavia no hay documentos guardados.</td></tr>';
}

$("catalogForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("catalogStatus", "Cargando");
  const form = new FormData();
  form.append("business_id", businessId());
  form.append("file", $("catalogFile").files[0]);
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
  } catch (error) {
    setStatus("catalogStatus", "Error", "error");
    $("catalogResult").textContent = friendlyError(error.message);
  }
});

$("documentForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("documentStatus", "Analizando");
  const form = new FormData();
  form.append("business_id", businessId());
  form.append("supplier_name", $("supplierName").value.trim());
  form.append("extraction_provider", $("extractionProvider").value);
  form.append("persist_candidates", "true");
  form.append("file", $("documentFile").files[0]);
  try {
    const data = await requestJson("/procurement/supplier-offers/from-document", {
      method: "POST",
      body: form,
    });
    const documentId = data.import_result.document.id;
    $("documentId").value = documentId;
    $("documentResult").textContent = summarizeAnalysis(data);
    setStatus("documentStatus", "Listo", "ok");
    await loadMatches();
    await loadDocuments();
  } catch (error) {
    setStatus("documentStatus", "Error", "error");
    $("documentResult").textContent = friendlyError(error.message);
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

$("loadDocuments").addEventListener("click", async () => {
  $("loadDocuments").disabled = true;
  try {
    await loadDocuments();
  } catch (error) {
    $("summary").textContent = friendlyError(error.message);
  } finally {
    $("loadDocuments").disabled = false;
  }
});

$("matchStatusFilter").addEventListener("change", () => {
  renderMatches();
});

$("matchesBody").addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const action = button.dataset.action;
  const id = button.dataset.id;
  if (action === "open-document") {
    $("documentId").value = id;
    await loadMatches();
    await loadDocuments();
    return;
  }
  button.disabled = true;
  try {
    let feedback = null;
    if (action === "correct") {
      const productId = window.prompt("ID del producto correcto");
      if (!productId) return;
      feedback = await requestJson(`/procurement/product-matches/${id}/correct`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          business_id: businessId(),
          product_id: productId,
          reviewed_by_user_id: "web-user",
          notes: "Corregido desde mini frontend",
        }),
      });
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
  } catch (error) {
    $("summary").textContent = friendlyError(error.message);
  } finally {
    button.disabled = false;
  }
});

loadDocuments().catch((error) => {
  $("documentsSummary").textContent = friendlyError(error.message);
});
