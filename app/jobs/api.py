import hashlib
import json
import re
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.jobs.repository import BackgroundJobRepository, IdempotencyConflict
from app.jobs.schemas import JobListResponse, JobResponse, JobStatus
from app.jobs.storage import JobFileStorage
from app.modules.procurement.analysis_pipeline_schemas import AnalysisParameters


def job_response(job):
    path = f"/api/procurement/supplier-offer-analysis-jobs/{job.id}"
    query = "?business_id=" + quote(job.business_id, safe="")
    return JobResponse(
        job_id=job.id,
        status=job.status,
        phase=job.phase,
        created_at=job.created_at,
        updated_at=job.updated_at,
        error_code=job.error_code,
        error_message=job.error_message,
        status_url=path + query,
        result_url=path + "/result" + query,
    )


def install_job_routes(api, *, config, session_factory, capabilities_factory):
    def components(request):
        if not config.jobs_enabled or not config.web_auth_enabled or session_factory is None:
            raise HTTPException(503, "Los trabajos de análisis todavía no están habilitados.")
        tenant = getattr(request.state, "tenant_context", None)
        if tenant is None:
            raise HTTPException(401, "Ingresá nuevamente para continuar.")
        return (
            tenant,
            BackgroundJobRepository(session_factory, lease_seconds=config.job_lease_seconds),
        )

    def owned(request, job_id, *, include_data=False):
        tenant, repository = components(request)
        job = repository.get_owned(
            job_id, tenant.access.business_id, tenant.access.user_id, include_data=include_data
        )
        if job is None:
            raise HTTPException(404, "Trabajo no encontrado.")
        return job

    @api.post(
        "/procurement/supplier-offer-analysis-jobs", status_code=202, response_model=JobResponse
    )
    def create_job(
        request: Request,
        file: Annotated[UploadFile, File()],
        business_id: Annotated[str, Form()],
        supplier_name: Annotated[str | None, Form()] = None,
        extraction_provider: Annotated[str, Form()] = "auto",
        max_candidates: Annotated[int, Form(ge=1, le=50)] = 1,
        persist_candidates: Annotated[bool, Form()] = True,
        analysis_method: Annotated[str, Form()] = "openai_structured",
        identity_probability: Annotated[float, Form(ge=0, le=1)] = 0.85,
        price_probability: Annotated[float, Form(ge=0, le=1)] = 0.85,
        decision_confidence: Annotated[float, Form(ge=0, le=1)] = 0.85,
        decision_margin: Annotated[float, Form(ge=0, le=1)] = 0.10,
        idempotency_key: Annotated[str | None, Header()] = None,
    ):
        tenant, repository = components(request)
        if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key):
            raise HTTPException(400, "Indicá una clave de idempotencia válida.")
        filename = Path((file.filename or "").replace("\\", "/")).name
        if not filename.lower().endswith(".pdf") or len(filename) > 255:
            raise HTTPException(422, "Elegí un archivo PDF con un nombre válido.")
        if extraction_provider not in {"auto", "openai", "local_text"}:
            raise HTTPException(422, "Proveedor de extracción no admitido.")
        try:
            parameters = AnalysisParameters(
                business_id=business_id,
                supplier_name=supplier_name,
                extraction_provider=extraction_provider,
                max_candidates=max_candidates,
                persist_candidates=persist_candidates,
                analysis_method=analysis_method,
                identity_probability=identity_probability,
                price_probability=price_probability,
                decision_confidence=decision_confidence,
                decision_margin=decision_margin,
            )
        except ValidationError as exc:
            raise HTTPException(422, "Parámetros de análisis inválidos.") from exc
        if extraction_provider != "local_text":
            capability = next(
                (
                    row
                    for row in capabilities_factory().methods
                    if row.id == parameters.analysis_method
                ),
                None,
            )
            if capability is None or not capability.available:
                raise HTTPException(503, "El método de análisis no está disponible.")
        if (
            parameters.analysis_method == "openai_blocks_jev"
            and extraction_provider == "local_text"
        ):
            raise HTTPException(422, "Este método requiere lectura con OpenAI.")
        storage = JobFileStorage(config.job_files_dir)
        try:
            stored = storage.save(file.file)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        values = parameters.model_dump(mode="json")
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "parameters": values,
                    "file_sha256": stored.sha256,
                    "filename": filename,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        try:
            job, created = repository.create(
                business_id=tenant.access.business_id,
                created_by_user_id=tenant.access.user_id,
                job_type="supplier_offer_analysis_v1",
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                file_key=stored.key,
                file_sha256=stored.sha256,
                source_filename=filename,
                parameters=values,
            )
        except IdempotencyConflict as exc:
            storage.remove(stored.key)  # Definitive rollback; this file is unreferenced.
            raise HTTPException(
                409, "La clave ya corresponde a otro archivo o parámetros."
            ) from exc
        # For an uncertain DB commit, leave the file for reconciliation. Never
        # delete a potentially accepted upload merely because its response was lost.
        if not created:
            storage.remove(stored.key)
        return job_response(job)

    @api.get("/procurement/supplier-offer-analysis-jobs", response_model=JobListResponse)
    def list_jobs(
        request: Request,
        business_id: str,
        status: JobStatus | None = None,
        idempotency_key: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        tenant, repository = components(request)
        jobs = repository.list_owned(
            tenant.access.business_id,
            tenant.access.user_id,
            status=status,
            idempotency_key=idempotency_key,
            limit=limit + 1,
            offset=offset,
        )
        return JobListResponse(
            jobs=[job_response(job) for job in jobs[:limit]],
            next_offset=offset + limit if len(jobs) > limit else None,
        )

    @api.get("/procurement/supplier-offer-analysis-jobs/{job_id}", response_model=JobResponse)
    def get_job(request: Request, job_id: str, business_id: str):
        return job_response(owned(request, job_id))

    @api.get("/procurement/supplier-offer-analysis-jobs/{job_id}/result")
    def get_job_result(request: Request, job_id: str, business_id: str):
        job = owned(request, job_id, include_data=True)
        if job.status != "succeeded" or job.result is None:
            return JSONResponse(
                status_code=409,
                content={"detail": "El resultado no está disponible.", "status": job.status},
            )
        return JSONResponse(content={**job.result, "analysis_job_id": job.id})

    @api.post("/procurement/supplier-offer-analysis-jobs/{job_id}/continue",
              response_model=JobResponse)
    def continue_job(request: Request, job_id: str, business_id: str, source_document_id: str):
        tenant, repository = components(request)
        try:
            return job_response(repository.continue_owned(
                job_id, tenant.access.business_id, tenant.access.user_id,
                source_document_id=source_document_id,
            ))
        except LookupError as exc:
            raise HTTPException(404, "Trabajo no disponible.") from exc
        except ValueError as exc:
            raise HTTPException(409, "No hay lecturas pendientes que se puedan continuar.") from exc
