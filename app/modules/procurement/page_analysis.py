"""One page pipeline: bounded reading, recovery, rules and optional identity Decisions."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

from app.modules.procurement.analysis_contracts import (
    AnalysisLedger,
    AnalysisMethod,
    AnalysisSummary,
    DecisionAuditRecord,
    DocumentReadRequest,
    DocumentReadResult,
    OfferDecisionResult,
    PageRecoveryRecord,
    PendingBlock,
    ProviderCallRecord,
    ReaderStructuredExtractionV2,
    ReaderStructuredExtractionV3,
    TokenUsage,
)
from app.modules.procurement.decision_policy import (
    POLICY_VERSION,
    DocumentDecisionPolicy,
    build_offer_decision_request,
)
from app.modules.procurement.offer_evidence import (
    StructuredProposalAssembler,
    usable_evidence,
    validate_metadata_size,
)
from app.modules.procurement.page_analysis_schemas import PageAnalysisCheckpoint, PageWork
from app.modules.procurement.page_evidence import (
    consolidate_observations,
    observation_key,
    page_blocks,
)
from app.modules.procurement.schemas import ExtractedSupplierOfferItem, SupplierOfferExtraction
from app.providers.documents.chunking import _page_with_used_images

VERSION = "page_pipeline_v3"


@contextmanager
def work_attachment(source, work):
    """Crop PDF viewports with overlap, using the existing PDF dependency only."""
    if source.suffix.lower() != ".pdf":
        yield source
        return
    with TemporaryDirectory(prefix="stock-ai-page-") as directory:
        reader = PdfReader(source)
        page = copy(_page_with_used_images(reader.pages[work.page - 1]))
        if work.region is not None:
            page.transfer_rotation_to_content()
            left, bottom, right, top = map(float, page.mediabox)
            middle_x, middle_y = (left + right) / 2, (bottom + top) / 2
            overlap_x, overlap_y = (right - left) * 0.05, (top - bottom) * 0.05
            col, row = work.region % 2, work.region // 2
            viewport = RectangleObject(
                [
                    left if col == 0 else middle_x - overlap_x,
                    middle_y - overlap_y if row == 0 else bottom,
                    middle_x + overlap_x if col == 0 else right,
                    top if row == 0 else middle_y + overlap_y,
                ]
            )
            page.mediabox = viewport
            page.cropbox = viewport
        path = Path(directory) / "page.pdf"
        writer = PdfWriter()
        writer.add_page(page)
        writer.write(path)
        writer.close()
        yield path


class PageOfferAnalysisService:
    supports_page_checkpoints = True

    def __init__(
        self,
        *,
        reader,
        candidate_retriever=None,
        decision_provider=None,
        max_calls=100,
        timeout_seconds=60,
        max_metadata_bytes=4_000_000,
        chunk_workers=2,
    ):
        self.reader = reader
        self.candidate_retriever = candidate_retriever
        self.decision_provider = decision_provider
        self.max_calls = max_calls
        self.timeout_seconds = timeout_seconds
        self.max_metadata_bytes = max_metadata_bytes
        self.chunk_workers = min(2, max(1, chunk_workers))

    def supports(self, method):
        return method == AnalysisMethod.OPENAI_STRUCTURED

    def analyze(
        self,
        *,
        business_id,
        document_path,
        method,
        supplier_hint=None,
        decision_thresholds=None,
        checkpoint=None,
        save_checkpoint=None,
    ):
        if not business_id.strip() or not self.supports(method):
            raise ValueError("Unsupported business or analysis method")
        started = monotonic()
        document_path = Path(document_path)
        source_hash = hashlib.sha256(document_path.read_bytes()).hexdigest()
        config = {
            "version": VERSION,
            "supplier": supplier_hint,
            "model": getattr(self.reader, "_model", "injected"),
            "effort": getattr(self.reader, "reasoning_effort", "low"),
            "max_calls": self.max_calls,
            "timeout": self.timeout_seconds,
            "output_limit": getattr(self.reader, "max_output_tokens", None),
        }
        prompt_path = Path("app/assistant/prompts/supplier_offer_single_page_v4.md")
        config["prompt_sha256"] = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
        config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        if checkpoint:
            state = PageAnalysisCheckpoint.model_validate(checkpoint)
            if (
                state.business_id != business_id
                or state.source_sha256 != source_hash
                or state.config_sha256 != config_hash
                or state.source_filename != document_path.name
            ):
                raise ValueError("Checkpoint source/business/configuration mismatch")
            for work in state.work:
                if work.status == "inflight":
                    work.status = "uncertain"  # Never blindly repeat an uncertain paid call.
                if work.status == "uncertain" and work.result is None:
                    reading_id = work.reading_id or uuid4().hex
                    work.reading_id = reading_id
                    work.result = DocumentReadResult(
                        extraction=None,
                        call=ProviderCallRecord(
                            call_id=reading_id,
                            run_id=state.run_id,
                            reading_id=reading_id,
                            provider="openai",
                            model=config["model"],
                            prompt_version="supplier_offer_single_page_v4",
                            schema_version="supplier_offer_reader_v3",
                            reasoning_effort=config["effort"],
                            analysis_method=method,
                            phase="initial" if work.region is None else "reread",
                            start_page=work.page,
                            end_page=work.page,
                            status="error",
                            safe_error="provider_result_uncertain",
                        ),
                    )
        else:
            count = (
                len(PdfReader(document_path).pages)
                if (document_path.suffix.lower() == ".pdf")
                else 1
            )
            state = PageAnalysisCheckpoint(
                business_id=business_id,
                run_id=uuid4().hex,
                source_sha256=source_hash,
                source_filename=document_path.name,
                config_sha256=config_hash,
                page_count=count,
                work=[PageWork(page=n) for n in range(1, count + 1)],
                reserved_calls=0,
                reader_config=config,
            )
        prior_duration = state.duration_seconds
        # A worker restart must not reset the paid-call budget. Only explicit,
        # bounded continuation opens a new round in the job repository.
        if state.reserved_calls is None:
            state.reserved_calls = sum(w.status != "pending" for w in state.work)

        def save():
            state.duration_seconds = prior_duration + monotonic() - started
            validate_metadata_size(state.model_dump(mode="json"), max_bytes=self.max_metadata_bytes)
            if save_checkpoint:
                save_checkpoint(state.model_copy(deep=True))

        def read(work):
            reading_id = work.reading_id
            before = monotonic()
            try:
                with work_attachment(document_path, work) as attachment:
                    request = DocumentReadRequest(
                        document_path=attachment,
                        run_id=state.run_id,
                        reading_id=reading_id,
                        analysis_method=method,
                        phase="initial" if work.region is None else "reread",
                        source_filename=state.source_filename,
                        start_page=work.page,
                        end_page=work.page,
                        supplier_hint=supplier_hint,
                        timeout_seconds=self.timeout_seconds,
                    )
                    result = self.reader.read(request=request)
                if result.extraction is not None and not isinstance(
                    result.extraction, (ReaderStructuredExtractionV2, ReaderStructuredExtractionV3)
                ):
                    result.call.status = "error"
                    result.call.safe_error = "wrong_page_schema"
                    result.extraction = None
                call = result.call
                if (
                    call.run_id != state.run_id
                    or call.reading_id != reading_id
                    or call.analysis_method != method
                    or call.phase != request.phase
                    or call.start_page not in {None, work.page}
                    or call.end_page not in {None, work.page}
                ):
                    raise ValueError("Foreign reader result")
                call.start_page = call.end_page = work.page
                return result
            except Exception as exc:
                return DocumentReadResult(
                    extraction=None,
                    call=ProviderCallRecord(
                        call_id=reading_id,
                        run_id=state.run_id,
                        reading_id=reading_id,
                        provider="openai",
                        model=getattr(self.reader, "_model", "injected"),
                        prompt_version="supplier_offer_single_page_v4",
                        schema_version=VERSION,
                        analysis_method=method,
                        phase="initial" if work.region is None else "reread",
                        start_page=work.page,
                        end_page=work.page,
                        duration_seconds=monotonic() - before,
                        status="error",
                        safe_error=f"page_read_failed:{type(exc).__name__}",
                    ),
                )

        def run(work_list):
            # Save intent before transport. Completion writes stay on this worker.
            for offset in range(0, len(work_list), self.chunk_workers):
                remaining = max(0, self.max_calls - state.reserved_calls)
                batch = work_list[offset : offset + min(self.chunk_workers, remaining)]
                if not batch:
                    break
                for work in batch:
                    work.status = "inflight"
                    work.reading_id = uuid4().hex
                state.reserved_calls += len(batch)
                save()
                with ThreadPoolExecutor(max_workers=self.chunk_workers) as executor:
                    futures = {executor.submit(read, w): w for w in batch}
                    for future in as_completed(futures):
                        work = futures[future]
                        work.result = future.result()
                        work.status = (
                            "succeeded"
                            if (work.result.extraction and work.result.call.status == "success")
                            else "failed"
                        )
                        save()

        run([w for w in state.work if w.status == "pending" and w.region is None])
        for work in list(state.work):
            if work.region is not None or work.status in {"pending", "uncertain"}:
                continue
            reasons = ["reader_failed"]
            if work.result and work.result.extraction:
                _, reasons = page_blocks(
                    extraction=work.result.extraction,
                    work=work,
                    run_id=state.run_id,
                    reading_id=work.result.call.reading_id,
                )
            if reasons and not any(
                w.page == work.page and w.region is not None for w in state.work
            ):
                state.work.extend(PageWork(page=work.page, region=n) for n in range(4))
        save()
        run([w for w in state.work if w.status == "pending" and w.region is not None])
        save()
        resolving_started = monotonic()
        result = self._resolve(state, supplier_hint, decision_thresholds, save_checkpoint=save)
        save()
        result.document_metadata["analysis"]["duration_seconds"] = state.duration_seconds
        result.document_metadata["analysis_execution"]["resolution_duration_seconds"] = (
            monotonic() - resolving_started
        )
        self.continuation_checkpoint = (
            state
            if (
                state.continuation_round < 2
                and any(
                    w.status == "pending" or w.status == "failed" and w.region is not None
                    for w in state.work
                )
            )
            else None
        )
        return result

    def _resolve(self, state, supplier_hint, thresholds, save_checkpoint=None):
        ledger = AnalysisLedger(
            run_id=state.run_id,
            business_id=state.business_id,
            method=AnalysisMethod.OPENAI_STRUCTURED,
            policy={
                "version": VERSION, "identity_policy": POLICY_VERSION,
                "decisions_prompt_hash": getattr(self.decision_provider, "config_id", None),
            },
        )
        assembler = StructuredProposalAssembler()
        policy = DocumentDecisionPolicy(thresholds=thresholds, require_identity_evidence=True)
        if self.decision_provider:
            self.decision_provider.reservations = state.identity_reservations
            self.decision_provider.halted = state.identity_decisions_halted or any(
                value is None for value in state.identity_decisions.values()
            )
        supplier = supplier_hint
        page_reasons = {}
        offers = []
        seen = set()
        for number in range(1, state.page_count + 1):
            works = [w for w in state.work if w.page == number]
            successful = [w for w in works if w.status == "succeeded"]
            reasons = []
            # Recoveries have more local detail; keep them first during consolidation.
            for work in sorted(successful, key=lambda w: w.region is None):
                supplier = supplier or work.result.extraction.supplier_name
                blocks, flags = page_blocks(
                    extraction=work.result.extraction,
                    work=work,
                    run_id=state.run_id,
                    reading_id=work.result.call.reading_id,
                )
                if work.region is not None:
                    reasons.extend(flags)
                for block in blocks:
                    ledger.blocks.append(block)
                    assembled = assembler.assemble_interpretation(
                        business_id=state.business_id,
                        block=block,
                        classification=None,
                        candidates=[],
                    )
                    for offer in assembled.offers:
                        key = (number, observation_key(offer))
                        if key in seen:
                            continue
                        seen.add(key)
                        block.derived_offer_ids.append(offer.offer_id)
                        offers.append(offer)
            initial = next(w for w in works if w.region is None)
            regions = [w for w in works if w.region is not None]
            if initial.status == "uncertain":
                reasons.append("provider_result_uncertain")
            if regions:
                if any(w.status != "succeeded" for w in regions):
                    reasons.append("recovery_incomplete")
            elif initial.status != "succeeded":
                reasons.append("reader_failed")
            elif initial.result:
                _, flags = page_blocks(
                    extraction=initial.result.extraction,
                    work=initial,
                    run_id=state.run_id,
                    reading_id=initial.result.call.reading_id,
                )
                reasons.extend(flags)
            estimates = [
                p.coverage.estimated_offer_blocks
                for w in successful
                if w.region is None
                for p in w.result.extraction.pages
                if p.coverage.estimated_offer_blocks is not None
            ]
            if estimates and sum(
                b.page_number == number and bool(b.derived_offer_ids) for b in ledger.blocks
            ) < max(estimates):
                reasons.append("low_block_coverage")
            page_reasons[number] = list(dict.fromkeys(reasons))
            ledger.pages.append(
                PageRecoveryRecord(
                    page_number=number,
                    initial_done=bool(successful),
                    reading_ids=[w.result.call.reading_id for w in successful],
                    reread_count=int(bool(regions)),
                    reason_codes=page_reasons[number],
                    status="exhausted" if reasons else "recovered" if regions else "read",
                )
            )
        ledger.calls = [*state.previous_calls, *[w.result.call for w in state.work if w.result]]
        by_id = {b.block_id: b for b in ledger.blocks}
        offers = [
            offer
            for offer, _ in consolidate_observations(
                [(offer, by_id[offer.block_id]) for offer in offers]
            )
        ]
        for offer in offers:
            if "possible_duplicate_observation" in offer.reason_codes:
                page = by_id[offer.block_id].page_number
                if "possible_duplicate_observation" not in page_reasons[page]:
                    page_reasons[page].append("possible_duplicate_observation")
                    ledger.pages[page - 1].reason_codes = page_reasons[page]
                    ledger.pages[page - 1].status = "exhausted"
        found = (
            self.candidate_retriever(
                business_id=state.business_id,
                offers=offers,
                supplier_name=supplier,
                internal_top_k=12,
            )
            if self.candidate_retriever and offers
            else {}
        )
        blocks = {b.block_id: b for b in ledger.blocks}
        final = []
        for offer in offers:
            block = blocks[offer.block_id]
            request = build_offer_decision_request(
                business_id=state.business_id,
                block=block,
                offer=offer,
                candidates=found.get(offer.offer_id, []),
                policy=policy,
            )
            assessed = policy.validate_decision(request=request, decision=None).offer
            # One coordinator, one identity resolution. Price never goes to Decisions.
            identity_request = request.model_copy(update={
                "questions": [q for q in request.questions if q.question_id.endswith(":identity")],
            })
            prepare = getattr(self.decision_provider, "prepare_request", None)
            if callable(prepare) and identity_request.questions:
                identity_request = prepare(identity_request)
            if (self.decision_provider and assessed.identity_status == "pending"
                    and identity_request.questions and usable_evidence(assessed, block)):
                provider = self.decision_provider
                key = provider.cache_key(identity_request)

                def reserve_decision(key=key):
                    state.identity_decisions[key] = None
                    if save_checkpoint:
                        save_checkpoint()

                if key in state.identity_decisions:
                    decision = state.identity_decisions[key] or OfferDecisionResult(
                        offer_id=offer.offer_id,
                        reason_codes=["decisions_uncertain_not_retried"],
                    )
                    decision = decision.model_copy(deep=True)
                    decision.offer_id = offer.offer_id
                    for choice in decision.decisions:
                        choice.question_id = identity_request.questions[0].question_id
                    for member in decision.memberships:
                        for purpose in ("compatibility", "inclusion"):
                            getattr(member, purpose).question_id = (
                                f"{offer.offer_id}:{member.candidate_id}:{purpose}"
                            )
                    for call in decision.calls:
                        if call.run_id != state.run_id:
                            call.source_run_id = call.source_run_id or call.run_id
                            call.run_id = state.run_id
                else:
                    decision = provider.decide_offer(
                        request=identity_request, before_send=reserve_decision,
                    )
                    state.identity_decisions_halted = provider.halted
                    if decision.calls:
                        state.identity_decisions[key] = decision
                        if save_checkpoint:
                            save_checkpoint()
                assessed = policy.validate_decision(
                    request=identity_request, decision=decision,
                ).offer
                recorded = {c.call_id for c in ledger.calls}
                ledger.calls.extend(c for c in decision.calls if c.call_id not in recorded)
                ledger.decision_audit.append(DecisionAuditRecord(
                    run_id=state.run_id, reading_id=block.reading_id,
                    block_id=block.block_id, offer_id=offer.offer_id, phase="decision",
                    questions=identity_request.questions if decision.calls else (
                        identity_request.questions[:1]
                    ), decisions=decision.decisions,
                    memberships=decision.memberships,
                    call_ids=[c.call_id for c in decision.calls],
                    reason_codes=assessed.reason_codes,
                    status="success" if decision.decisions else "error",
                ))
            ledger.interpretations.append(assessed)
            if not usable_evidence(assessed, block):
                continue
            final.append(
                ExtractedSupplierOfferItem(
                    raw_name=assessed.raw_name,
                    brand=assessed.brand,
                    unit_size=assessed.unit_size,
                    unit=assessed.unit,
                    offer_price=assessed.offer_price,
                    currency=assessed.currency or "ARS",
                    tax_included=assessed.tax_included,
                    price_type=assessed.price_type,
                    package_quantity=assessed.package_quantity
                    if assessed.price_type == "pack"
                    else None,
                    page_number=block.page_number,
                    selected_source="analysis_v2",
                    provider_observations={
                        "analysis_v2": {
                            "offer_id": assessed.offer_id,
                            "block_id": block.block_id,
                            "interpretation": assessed.model_dump(mode="json"),
                            "commercial_validation": {
                                "version": VERSION,
                                "tax_basis_confirmed": False,
                            },
                        }
                    },
                )
            )
        emitted = {i.provider_observations["analysis_v2"]["offer_id"] for i in final}
        for block in ledger.blocks:
            if not block.derived_offer_ids or any(
                i not in emitted for i in block.derived_offer_ids
            ):
                # Identical repeat observations are audit-only, not additional missing offers.
                if block.proposals and not block.derived_offer_ids:
                    continue
                ledger.pending_blocks.append(
                    PendingBlock(
                        block_id=block.block_id,
                        page_number=block.page_number,
                        order_on_page=block.order_on_page,
                        text=block.full_text,
                        reason_codes=["missing_usable_offer"],
                        reason="Oferta conservada; faltan datos para comparar.",
                    )
                )
        read = {p.page_number for p in ledger.pages if p.initial_done}
        attempted = {w.page for w in state.work if w.status != "pending"}
        incomplete = [n for n, reasons in page_reasons.items() if reasons]
        current = [o for o in ledger.interpretations if o.offer_id in emitted]
        usage = None
        if ledger.calls and all(c.usage for c in ledger.calls):
            usage = TokenUsage(
                **{
                    field: sum(getattr(c.usage, field) for c in ledger.calls)
                    if all(getattr(c.usage, field) is not None for c in ledger.calls)
                    else None
                    for field in TokenUsage.model_fields
                }
            )
        summary = AnalysisSummary(
            run_id=state.run_id,
            method=ledger.method,
            status="partial"
            if incomplete
            or ledger.pending_blocks
            or any(
                o.identity_status != "confirmed" or o.price_status != "confirmed" for o in current
            )
            else "completed",
            page_count=state.page_count,
            read_page_count=len(read),
            attempted_page_count=len(attempted),
            incomplete_page_numbers=incomplete,
            unattempted_page_numbers=sorted(set(range(1, state.page_count + 1)) - attempted),
            failed_page_numbers=sorted(attempted - read),
            review_page_numbers=incomplete,
            provider_call_count=len(ledger.calls),
            local_rejection_count=0,
            observed_block_count=len(ledger.blocks),
            interpreted_offer_count=len(final),
            pending_block_count=len(ledger.pending_blocks),
            unlinked_offer_count=sum(o.product_id is None for o in current),
            identity_review_offer_count=sum(o.identity_status == "pending" for o in current),
            price_review_offer_count=sum(o.price_status == "pending" for o in current),
            comparable_offer_count=sum(
                o.identity_status == "confirmed" and o.price_status == "confirmed" for o in current
            ),
            group_offer_count=sum(o.identity_scope == "product_group" for o in current),
            group_compatible_offer_count=sum(
                o.group_membership_status == "compatible" for o in current
            ),
            group_confirmed_offer_count=sum(
                o.group_membership_status == "confirmed" for o in current
            ),
            duration_seconds=state.duration_seconds,
            usage=usage,
        )
        metadata = {
            "analysis_v2": True,
            "analysis": summary.model_dump(mode="json"),
            "analysis_ledger": ledger.model_dump(mode="json"),
            "needs_review_pages": incomplete,
            "analysis_execution": {
                "version": VERSION,
                "source_sha256": state.source_sha256,
                "reader_config": state.reader_config,
                "identity_candidates": {
                    k: [c.model_dump(mode="json") for c in v] for k, v in found.items()
                },
                "identity_decisions_budget": {
                    "max_calls": getattr(self.decision_provider, "max_calls", 0),
                    "max_spend_usd": str(self.decision_provider.max_spend_usd)
                    if self.decision_provider else None,
                    "reserved_calls": len(state.identity_reservations),
                    "reservations_usd": state.identity_reservations,
                    "halted": state.identity_decisions_halted,
                },
            },
            "recovery_pending": {
                "continuation_available": (
                    state.continuation_round < 2
                    and any(
                        w.status == "pending" or w.status == "failed" and w.region is not None
                        for w in state.work
                    )
                ),
                "reason": "bounded_page_result",
                "page_numbers": incomplete,
            },
        }
        metadata["analysis_execution"]["parent_run_id"] = state.parent_run_id
        validate_metadata_size(metadata, max_bytes=self.max_metadata_bytes)
        return SupplierOfferExtraction(
            supplier_name=supplier,
            source_filename=state.source_filename,
            items=final,
            document_metadata=metadata,
        )
