import hashlib
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from app import main
from app.db.models import BackgroundJobModel
from app.jobs.worker import AnalysisWorker
from app.modules.procurement.analysis_contracts import DocumentReadResult, ProviderCallRecord
from app.modules.procurement.page_analysis import PageOfferAnalysisService
from app.modules.procurement.page_analysis_schemas import (
    PageAnalysisCheckpoint,
    PageWork,
    PipelineCheckpoint,
)


def saved_pages():
    return PipelineCheckpoint(
        business_id="one",
        parameters_sha256="a" * 64,
        analysis=PageAnalysisCheckpoint(
            business_id="one",
            run_id="original",
            source_sha256="b" * 64,
            source_filename="offers.pdf",
            config_sha256="c" * 64,
            page_count=2,
            work=[PageWork(page=1, status="succeeded"), PageWork(page=2)],
        ),
    )


def test_continuation_is_owned_idempotent_and_preserves_prior_result(queued_job):
    repository, job = queued_job()
    attempt = repository.claim()
    repository.change_attempt(
        job.id,
        attempt.attempt_token,
        checkpoint={
            "continuation_checkpoint": saved_pages().model_dump(mode="json"),
        },
    )
    repository.complete(
        job.id,
        attempt.attempt_token,
        lambda _: SimpleNamespace(model_dump=lambda **_: {"document": "old"}),
    )
    with pytest.raises(LookupError):
        repository.continue_owned(job.id, "two", "user-two")
    with pytest.raises(LookupError):
        repository.continue_owned(job.id, "one", "colleague")
    continued = repository.continue_owned(job.id, "one", "user-one")
    state = PipelineCheckpoint.model_validate(continued.checkpoint).analysis
    assert state.parent_run_id == "original" and state.run_id != "original"
    assert state.continuation_round == 1
    assert continued.result == {"document": "old"}
    repeated = repository.continue_owned(job.id, "one", "user-one")
    assert repeated.checkpoint == continued.checkpoint
    assert repeated.attempts == 0
    with pytest.raises(ValueError, match="version"):
        repository.continue_owned(job.id, "one", "user-one", source_document_id="stale")


def test_partial_page_job_continues_without_repeating_completed_read(
    sessions,
    storage,
    queued_job,
    monkeypatch,
):
    from app.modules.procurement.analysis_contracts import ReaderStructuredExtractionV3

    calls = []

    class Reader:
        def read(self, *, request):
            calls.append(request.start_page)
            blocks = []
            for name in ["Arroz 1kg", "Azúcar 1kg"]:
                fields = {"raw_name": name, "offer_price": "$1000", "price_type": "por unidad"}
                blocks.append(
                    {
                        "fragments": [
                            {"text": f"{name} $1000 por unidad", "legibility": "readable"}
                        ],
                        "page_condition_indices": [],
                        "warnings": [],
                        "proposals": [
                            {
                                "raw_name": name,
                                "brand": None,
                                "unit_size": None,
                                "unit": None,
                                "offer_price": 1000,
                                "currency": None,
                                "tax_included": None,
                                "price_type": "unit",
                                "package_quantity": None,
                                "retail_contents_count": None,
                                "minimum_purchase": None,
                                "confidence_score": None,
                                "reference_price": None,
                                "reference_unit": None,
                                "field_evidence": [
                                    {
                                        "field": field,
                                        "refs": [
                                            {"scope": "block", "fragment_index": 0, "quote": quote}
                                        ],
                                    }
                                    for field, quote in fields.items()
                                ],
                                "warnings": [],
                            }
                        ],
                    }
                )
            extraction = ReaderStructuredExtractionV3(
                supplier_name="Proveedor",
                warnings=[],
                pages=[
                    {
                        "page_number": 1,
                        "page_conditions": [],
                        "blocks": blocks,
                        "coverage": {
                            "estimated_offer_blocks": 2,
                            "needs_visual_review": False,
                            "warnings": [],
                        },
                    }
                ],
            )
            return DocumentReadResult(
                extraction=extraction,
                call=ProviderCallRecord(
                    call_id=request.reading_id,
                    run_id=request.run_id,
                    reading_id=request.reading_id,
                    provider="openai",
                    model="fixture",
                    prompt_version="fixture",
                    schema_version="v3",
                    analysis_method=request.analysis_method,
                    phase=request.phase,
                    start_page=request.start_page,
                    end_page=request.end_page,
                    status="success",
                ),
            )

    repository, job = queued_job()
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    writer.add_blank_page(width=600, height=800)
    writer.write(storage.path(job.file_key))
    with sessions() as session:
        stored = session.get(BackgroundJobModel, job.id)
        stored.file_sha256 = hashlib.sha256(storage.path(job.file_key).read_bytes()).hexdigest()
        stored.parameters = {**stored.parameters, "extraction_provider": "auto"}
        session.commit()
    service = PageOfferAnalysisService(reader=Reader(), max_calls=1)
    monkeypatch.setattr(main, "build_document_analysis_service", lambda: service)
    pipeline = main.build_supplier_offer_analysis_pipeline()
    worker = AnalysisWorker(repository, storage, pipeline)
    assert worker.run_once()
    first = repository.get_owned(job.id, "one", "user-one")
    assert first.status == "succeeded" and first.checkpoint
    original_id = first.result["import_result"]["document"]["id"]
    assert len(first.result["import_result"]["items"]) == 2
    repository.continue_owned(job.id, "one", "user-one")
    assert worker.run_once()
    second = repository.get_owned(job.id, "one", "user-one")
    assert second.status == "succeeded" and second.checkpoint is None
    assert len(second.result["import_result"]["items"]) == 4
    assert second.result["import_result"]["document"]["id"] != original_id
    assert (
        second.result["import_result"]["document"]["metadata"]["analysis_execution"][
            "parent_run_id"
        ]
        == original_id
    )
    assert calls == [1, 2]
