"""Shared extraction and persistence pipeline for synchronous API and durable worker."""

import hashlib
import json
import logging
import shutil
import tempfile
from pathlib import Path
from time import monotonic
from uuid import uuid4

from app.modules.procurement.analysis_contracts import AnalysisMethod, AnalysisSummary, PendingBlock
from app.modules.procurement.analysis_pipeline_schemas import AnalysisParameters, PreparedAnalysis
from app.modules.procurement.analysis_reporting import summary_from_metadata
from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.schemas import (
    SupplierOfferCompareResponse,
    SupplierOfferDocumentAnalysisResponse,
)
from app.modules.procurement.supplier_offers import SupplierOfferService
from app.providers.documents.fallback import DocumentExtractionUnavailableError

logger = logging.getLogger("uvicorn.error")


class AnalysisPipelineError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class SupplierOfferAnalysisPipeline:
    supports_page_checkpoints = True

    def __init__(
        self,
        *,
        analysis_service_factory,
        document_provider_factory,
        capabilities_factory,
        pack_reviewer_factory,
        log_stage,
        safe_filename,
        max_metadata_bytes,
        pack_review,
    ):
        self.analysis_service_factory = analysis_service_factory
        self.document_provider_factory = document_provider_factory
        self.capabilities_factory = capabilities_factory
        self.pack_reviewer_factory = pack_reviewer_factory
        self.log_stage = log_stage
        self.safe_filename = safe_filename
        self.max_metadata_bytes = max_metadata_bytes
        self.pack_review = pack_review

    def prepare(
        self, file, parameters: AnalysisParameters, *, checkpoint=None, save_checkpoint=None
    ) -> PreparedAnalysis:
        business_id = parameters.business_id
        supplier_name = parameters.supplier_name
        extraction_provider = parameters.extraction_provider
        persist_candidates = parameters.persist_candidates
        analysis_method = parameters.analysis_method
        identity_probability = parameters.identity_probability
        price_probability = parameters.price_probability
        decision_confidence = parameters.decision_confidence
        decision_margin = parameters.decision_margin
        if not file.filename:
            raise AnalysisPipelineError(status_code=400, detail="filename is required.")
        if not business_id.strip():
            raise AnalysisPipelineError(status_code=422, detail="Falta indicar el negocio.")
        try:
            method = AnalysisMethod(analysis_method)
        except ValueError as exc:
            raise AnalysisPipelineError(
                status_code=422, detail="Método de análisis desconocido."
            ) from exc
        analysis_service = self.analysis_service_factory()
        if method == AnalysisMethod.OPENAI_BLOCKS_JEV:
            capability = next(c for c in self.capabilities_factory().methods if c.id == method)
            if not capability.available:
                raise AnalysisPipelineError(status_code=503, detail=capability.unavailable_reason)
            if extraction_provider not in {"auto", "openai"}:
                raise AnalysisPipelineError(
                    status_code=422, detail="Este método requiere lectura con OpenAI."
                )

        request_id = uuid4().hex[:12]
        request_started_at = monotonic()
        stage_started_at = request_started_at
        logger.info(
            "catalog_analysis request_id=%s stage=started provider=%s persist_candidates=%s",
            request_id,
            extraction_provider,
            persist_candidates,
        )
        use_v2 = (
            extraction_provider in {"auto", "openai"}
            and analysis_service is not None
            and analysis_service.supports(method)
        )
        if extraction_provider in {"auto", "openai"} and not use_v2:
            raise AnalysisPipelineError(
                status_code=503, detail="La lectura por página no está disponible."
            )
        provider = None if use_v2 else self.document_provider_factory(extraction_provider)
        latest_checkpoint = None
        original_filename = Path(file.filename).name

        with tempfile.TemporaryDirectory(prefix="stock-ai-offer-") as temp_dir:
            document_path = Path(temp_dir) / self.safe_filename(original_filename)
            with document_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            stage_started_at = self.log_stage(
                request_id,
                "upload_saved",
                stage_started_at,
                file_size_bytes=document_path.stat().st_size,
                file_type=document_path.suffix.lower() or "unknown",
            )

            try:
                if use_v2:
                    from app.modules.procurement.decision_policy import DecisionThresholds

                    page_kwargs = {}
                    if getattr(analysis_service, "supports_page_checkpoints", False):
                        from app.modules.procurement.page_analysis_schemas import PipelineCheckpoint

                        fingerprint = hashlib.sha256(
                            json.dumps(
                                parameters.model_dump(mode="json"),
                                sort_keys=True,
                            ).encode()
                        ).hexdigest()
                        saved = (
                            PipelineCheckpoint.model_validate(checkpoint) if checkpoint else None
                        )
                        if saved and (
                            saved.business_id != business_id
                            or saved.parameters_sha256 != fingerprint
                        ):
                            raise ValueError("Checkpoint parameters mismatch")

                        def save_pages(state):
                            nonlocal latest_checkpoint
                            latest_checkpoint = PipelineCheckpoint(
                                business_id=business_id,
                                parameters_sha256=fingerprint,
                                analysis=state,
                            )
                            if save_checkpoint:
                                save_checkpoint(latest_checkpoint)

                        page_kwargs = {
                            "checkpoint": saved.analysis if saved else None,
                            "save_checkpoint": save_pages,
                        }

                    extraction = analysis_service.analyze(
                        business_id=business_id,
                        document_path=document_path,
                        method=method,
                        supplier_hint=supplier_name,
                        decision_thresholds=DecisionThresholds(
                            identity_probability=identity_probability,
                            price_probability=price_probability,
                            confidence=decision_confidence,
                            margin=decision_margin,
                        ),
                        **page_kwargs,
                    )
                else:
                    extraction = provider.extract_supplier_offer(
                        document_path=document_path,
                        supplier_hint=supplier_name,
                    )
                stage_started_at = self.log_stage(
                    request_id,
                    "document_extracted",
                    stage_started_at,
                    item_count=len(extraction.items),
                    extraction_id=extraction.document_metadata.get("extraction_id", "none"),
                    provider_calls=(
                        len(extraction.document_metadata["analysis_ledger"]["calls"])
                        if use_v2
                        else extraction.document_metadata.get("initial_provider_calls", "unknown")
                    ),
                    retry_calls=(
                        sum(
                            c.get("phase") == "reread"
                            for c in extraction.document_metadata["analysis_ledger"]["calls"]
                        )
                        if use_v2
                        else extraction.document_metadata.get("retry_provider_calls", "unknown")
                    ),
                )
                if extraction_provider != "local_text" and not use_v2:
                    reviewer = self.pack_reviewer_factory()
                    self.pack_review(
                        extraction,
                        document_path=document_path,
                        reviewer=reviewer,
                    )
                    stage_started_at = self.log_stage(
                        request_id,
                        "pack_review_completed",
                        stage_started_at,
                        reviewed_count=extraction.document_metadata.get("pack_review_count", 0),
                        pending_count=extraction.document_metadata.get(
                            "pack_needs_review_count", 0
                        ),
                    )
            except DocumentExtractionUnavailableError as exc:
                logger.warning(
                    "catalog_analysis request_id=%s stage=document_extraction_failed "
                    "error_type=%s total_duration_ms=%.1f",
                    request_id,
                    type(exc).__name__,
                    (monotonic() - request_started_at) * 1000,
                )
                raise AnalysisPipelineError(
                    status_code=503,
                    detail=str(exc),
                ) from exc
            except Exception as exc:
                logger.exception(
                    "catalog_analysis request_id=%s stage=document_processing_failed "
                    "error_type=%s total_duration_ms=%.1f",
                    request_id,
                    type(exc).__name__,
                    (monotonic() - request_started_at) * 1000,
                )
                raise AnalysisPipelineError(
                    status_code=422,
                    detail=f"Document extraction failed: {exc}",
                ) from exc

        extraction.source_filename = original_filename
        resolved_supplier_name = extraction.supplier_name or supplier_name
        if not resolved_supplier_name:
            raise AnalysisPipelineError(
                status_code=422,
                detail="Supplier name could not be extracted. Send supplier_name.",
            )
        has_evidence = bool(
            extraction.document_metadata.get("analysis_ledger", {}).get("blocks")
            or extraction.document_metadata.get("analysis_ledger", {}).get("invalid_evidence")
        )
        if (
            not extraction.items
            and not extraction.document_metadata.get("needs_review_pages")
            and not has_evidence
        ):
            raise AnalysisPipelineError(
                status_code=422, detail="No supplier offer items were extracted."
            )

        if not use_v2 and extraction_provider in {"auto", "openai"}:
            # Record the real OpenAI path, without pretending legacy exposes V2 blocks/usage.
            summary = AnalysisSummary(
                run_id=request_id,
                method=method,
                status="partial"
                if extraction.document_metadata.get("needs_review_pages")
                else "completed",
                page_count=extraction.document_metadata.get("page_count", 1),
                observed_block_count=0,
                interpreted_offer_count=len(extraction.items),
                pending_block_count=0,
                price_review_offer_count=sum(
                    i.price_type.value == "unknown" for i in extraction.items
                ),
                review_page_numbers=extraction.document_metadata.get("needs_review_pages", []),
                duration_seconds=monotonic() - request_started_at,
            )
            extraction.document_metadata["analysis"] = summary.model_dump(mode="json")
            extraction.document_metadata["analysis_path"] = "legacy_openai"

        return PreparedAnalysis(
            business_id=business_id,
            supplier_name=resolved_supplier_name,
            extraction=extraction,
            request_id=request_id,
            elapsed_seconds=monotonic() - request_started_at,
            continuation_checkpoint=latest_checkpoint.model_dump(mode="json")
            if (latest_checkpoint and getattr(analysis_service, "continuation_checkpoint", None))
            else None,
        )

    def persist(
        self, prepared: PreparedAnalysis, parameters: AnalysisParameters, repository
    ) -> SupplierOfferDocumentAnalysisResponse:
        if prepared.business_id != parameters.business_id:
            raise ValueError("Checkpoint belongs to another business")
        business_id = prepared.business_id
        extraction = prepared.extraction
        resolved_supplier_name = prepared.supplier_name
        request_id = prepared.request_id
        max_candidates = parameters.max_candidates
        persist_candidates = parameters.persist_candidates
        request_started_at = monotonic() - prepared.elapsed_seconds
        stage_started_at = monotonic()
        supplier_offer_service = SupplierOfferService(
            repository, max_metadata_bytes=self.max_metadata_bytes
        )
        match_service = ProductMatchService(repository)
        import_result = supplier_offer_service.create_offer_from_extraction(
            business_id=business_id,
            supplier_name=resolved_supplier_name,
            extraction=extraction,
        )
        stage_started_at = self.log_stage(
            request_id,
            "offer_persisted",
            stage_started_at,
            item_count=len(import_result.items),
        )
        report = match_service.compare_supplier_offer(
            business_id=business_id,
            supplier_offer_document_id=import_result.document.id,
            max_candidates_per_item=max_candidates,
            trace_id=request_id,
        )
        stage_started_at = self.log_stage(
            request_id,
            "catalog_matched",
            stage_started_at,
            candidate_count=len(report.candidates),
        )
        persisted_count = 0
        if persist_candidates:
            persisted_count = len(match_service.save_supplier_offer_candidates(report=report))
        stage_started_at = self.log_stage(
            request_id,
            "candidates_persisted",
            stage_started_at,
            persisted_count=persisted_count,
        )
        self.log_stage(
            request_id,
            "completed",
            request_started_at,
            persisted_count=persisted_count,
            total_items=len(import_result.items),
        )
        stored_ledger = import_result.document.metadata.get("analysis_ledger", {})
        return SupplierOfferDocumentAnalysisResponse(
            import_result=import_result,
            comparison=SupplierOfferCompareResponse(
                report=report,
                persisted_count=persisted_count,
            ),
            extraction_warnings=extraction.warnings,
            analysis=summary_from_metadata(import_result.document.metadata),
            pending_blocks=[
                PendingBlock.model_validate(b) for b in stored_ledger.get("pending_blocks", [])
            ],
        )
