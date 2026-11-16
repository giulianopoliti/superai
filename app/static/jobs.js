(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  else root.StockJobs = factory({
    transport: root.StockTransport, storage: root.sessionStorage,
    scope: () => root.StockAuth.scope(),
    uuid: () => root.crypto.randomUUID(),
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  });
})(typeof window !== "undefined" ? window : globalThis, function createJobClient(deps) {
  const base = "/procurement/supplier-offer-analysis-jobs";
  let generation = 0;
  function read() { try { const rows = JSON.parse(deps.storage.getItem("stock-ai.jobs.v1") || "[]"); return Array.isArray(rows) ? rows.filter((row) => row && typeof row.key === "string" && typeof row.scope === "string" && typeof row.businessId === "string") : []; } catch { return []; } }
  function remember(reference) {
    const references = read().filter((row) => !(row.scope === reference.scope && row.key === reference.key));
    deps.storage.setItem("stock-ai.jobs.v1", JSON.stringify([...references, reference].slice(-100)));
  }
  function forget(key, scope) {
    deps.storage.setItem("stock-ai.jobs.v1", JSON.stringify(read().filter((row) => row.key !== key || row.scope !== scope)));
  }
  async function json(path, options) {
    const response = await deps.transport.fetch(path, options);
    const data = await response.json().catch((error) => { if (error.name === "AbortError") throw error; return {}; });
    if (!response.ok) { const error = new Error(data.detail || "No se pudo consultar el análisis."); error.status = response.status; throw error; }
    return data;
  }
  async function recoverKey(reference) {
    if (reference.jobId) {
      try { return await json(`${base}/${encodeURIComponent(reference.jobId)}?business_id=${encodeURIComponent(reference.businessId)}`); }
      catch (error) { if (error.status !== 404) throw error; forget(reference.key, reference.scope); return null; }
    }
    const data = await json(`${base}?business_id=${encodeURIComponent(reference.businessId)}&idempotency_key=${encodeURIComponent(reference.key)}`);
    return data.jobs?.[0] || null;
  }
  async function wait(job, reference, epoch) {
    let failures = 0;
    while (epoch === generation && deps.scope() === reference.scope) {
      try {
        if (job.status === "succeeded") {
          const result = await json(job.result_url);
          forget(reference.key, reference.scope);
          return result;
        }
        if (job.status === "failed") {
          forget(reference.key, reference.scope);
          throw Object.assign(new Error(job.error_message || "El análisis no pudo completarse."), { terminal: true });
        }
        await deps.sleep(Math.min(2000 * (1 + failures), 10000));
        if (epoch !== generation || deps.scope() !== reference.scope) break;
        job = await json(job.status_url);
        failures = 0;
      } catch (error) {
        if (error.terminal || [401, 403, 404].includes(error.status)) throw error;
        if (error.name === "AbortError") throw error;
        // A failed poll does not resubmit the PDF or cancel an accepted job.
        failures = Math.min(failures + 1, 4);
        await deps.sleep(Math.min(2000 * failures, 10000));
      }
    }
    throw new DOMException("Consulta detenida por cambio de sesión o comercio", "AbortError");
  }
  return {
    stop() { generation++; },
    async continueAnalysis(jobId, businessId, sourceDocumentId) {
      const epoch = generation;
      const reference = { key: jobId, jobId, businessId, scope: deps.scope() };
      // One explicit request; an uncertain POST is recovered by job ID, never repeated.
      remember(reference);
      let job;
      try {
        job = await json(`${base}/${encodeURIComponent(jobId)}/continue?business_id=${encodeURIComponent(businessId)}&source_document_id=${encodeURIComponent(sourceDocumentId)}`, { method: "POST" });
      } catch (error) {
        if (error.status && error.status < 500) { forget(reference.key, reference.scope); throw error; }
        job = await recoverKey(reference);
        if (!job || job.status === "succeeded") throw new Error("No se pudo confirmar la continuación. Revisá el trabajo antes de repetirla.");
      }
      return wait(job, reference, epoch);
    },
    async analyze(form, businessId) {
      const epoch = generation;
      const reference = { key: deps.uuid(), businessId, scope: deps.scope(), jobId: null };
      remember(reference); // Only IDs and scope, never files, tokens or full result data.
      let job;
      try {
        job = await json(base, { method: "POST", headers: { "Idempotency-Key": reference.key }, body: form });
      } catch (error) {
        if (error.status && error.status < 500) { forget(reference.key, reference.scope); throw error; }
        job = await recoverKey(reference); // Look up once; never automatically repeat POST.
        if (!job) throw new Error("No se pudo confirmar el envío. Al reingresar revisaremos si el trabajo fue recibido.");
      }
      reference.jobId = job.job_id;
      remember(reference);
      return wait(job, reference, epoch);
    },
    async recover(businessId, onResult) {
      const epoch = generation;
      const scope = deps.scope();
      const known = read().filter((row) => row.scope === scope && row.businessId === businessId);
      // Listing also recovers accepted jobs if browser reference storage was lost.
      const jobs = [];
      for (const status of ["queued", "running"]) {
        let offset = 0;
        do {
          if (epoch !== generation || scope !== deps.scope()) throw new DOMException("Consulta detenida", "AbortError");
          const data = await json(`${base}?business_id=${encodeURIComponent(businessId)}&status=${status}&limit=100&offset=${offset}`);
          jobs.push(...data.jobs);
          offset = data.next_offset;
        } while (offset !== null && offset !== undefined);
      }
      for (const reference of known) {
        if (!jobs.some((job) => job.job_id === reference.jobId)) {
          const recovered = await recoverKey(reference);
          if (recovered) { reference.jobId = recovered.job_id; remember(reference); jobs.push(recovered); }
        }
      }
      let lastFailure;
      for (const job of new Map(jobs.map((row) => [row.job_id, row])).values()) {
        const reference = known.find((row) => row.jobId === job.job_id) || {
          scope, businessId, key: job.job_id, jobId: job.job_id,
        };
        remember(reference);
        try {
          const result = await wait(job, reference, epoch);
          if (epoch === generation && scope === deps.scope()) await onResult(result);
        } catch (error) {
          if (error.name === "AbortError" || [401, 403].includes(error.status)) throw error;
          lastFailure = error;
        }
      }
      if (lastFailure) throw lastFailure;
    },
  };
});
