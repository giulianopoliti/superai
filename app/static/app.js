const $ = (id) => document.getElementById(id);

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

function rowTemplate(item) {
  const candidate = item.candidate;
  const offer = item.supplier_offer_item;
  const product = item.product;
  return `
    <tr>
      <td>${candidate.status}<br>${candidate.recommendation}</td>
      <td>${offer.raw_name}</td>
      <td>${money(offer.offer_price)}</td>
      <td>${product ? product.name : "Sin match"}</td>
      <td>${product ? money(product.current_cost) : ""}</td>
      <td>${money(candidate.cost_difference)}</td>
      <td>${money(candidate.confidence_score)}</td>
      <td>
        <div class="actions">
          <button data-action="accept" data-id="${candidate.id}">Aceptar</button>
          <button class="danger" data-action="reject" data-id="${candidate.id}">Rechazar</button>
          <button class="secondary" data-action="correct" data-id="${candidate.id}">Corregir</button>
        </div>
      </td>
    </tr>
  `;
}

async function loadMatches() {
  const documentId = $("documentId").value.trim();
  if (!documentId) return;
  const data = await requestJson(
    `/procurement/supplier-offers/${encodeURIComponent(documentId)}/matches?business_id=${encodeURIComponent(businessId())}`,
  );
  $("summary").textContent = `${data.candidates.length} sugerencias para revisar`;
  $("matchesBody").innerHTML = data.candidates.length
    ? data.candidates.map(rowTemplate).join("")
    : '<tr><td colspan="8" class="empty">No hay sugerencias guardadas.</td></tr>';
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
  } catch (error) {
    setStatus("documentStatus", "Error", "error");
    $("documentResult").textContent = friendlyError(error.message);
  }
});

$("loadMatches").addEventListener("click", async () => {
  try {
    await loadMatches();
  } catch (error) {
    $("summary").textContent = friendlyError(error.message);
  }
});

$("matchesBody").addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const action = button.dataset.action;
  const id = button.dataset.id;
  button.disabled = true;
  try {
    if (action === "correct") {
      const productId = window.prompt("ID del producto correcto");
      if (!productId) return;
      await requestJson(`/procurement/product-matches/${id}/correct`, {
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
      await requestJson(`/procurement/product-matches/${id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          business_id: businessId(),
          reviewed_by_user_id: "web-user",
          notes: action === "accept" ? "Aceptado desde mini frontend" : "Rechazado desde mini frontend",
        }),
      });
    }
    await loadMatches();
  } catch (error) {
    $("summary").textContent = friendlyError(error.message);
  } finally {
    button.disabled = false;
  }
});
