"""Request-scoped V2 orchestration. Providers never receive repositories."""

import hashlib
import json
import logging
import math
import re
import sys
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.modules.procurement.analysis_contracts import (
    WIRE_VERSION,
    AnalysisLedger,
    AnalysisMethod,
    AnalysisSummary,
    BlockClassificationRequest,
    BlockInterpretationResult,
    DecisionAuditRecord,
    DocumentReadRequest,
    EvidenceFragment,
    EvidenceRef,
    MinimumPurchase,
    OfferDecisionRequest,
    OfferInterpretation,
    OfferTextBlock,
    PageRecoveryRecord,
    PendingBlock,
    ProviderCallRecord,
    ReaderStructuredExtractionV2,
    TokenUsage,
)
from app.modules.procurement.decision_policy import DecisionThresholds, DocumentDecisionPolicy
from app.modules.procurement.normalization import normalize_text
from app.modules.procurement.schemas import ExtractedSupplierOfferItem, SupplierOfferExtraction
from app.providers.documents.chunking import PdfChunk, pdf_chunks

logger = logging.getLogger("uvicorn.error")


class AnalysisUnavailableError(ValueError):
    pass


class AnalysisMetadataTooLargeError(ValueError):
    pass


def validate_metadata_size(metadata, *, max_bytes=4_000_000):
    size = len(
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    )
    if size > max_bytes:
        raise AnalysisMetadataTooLargeError(
            "La evidencia supera el lÃ­mite de almacenamiento del anÃ¡lisis."
        )
    return size


def usable_evidence(offer, block):
    """Commercial fields must be supported, even when their price basis is pending."""
    from app.modules.procurement.decision_policy import published_price_evidence_valid

    if not published_price_evidence_valid(offer=offer, block=block):
        return False
    allowed = {f.fragment_id: f for f in block.fragments}
    allowed.update(
        {f.fragment_id: f for f in block.page_conditions if f.fragment_id in block.condition_refs}
    )
    texts = {}
    for field in ("raw_name", "offer_price"):
        refs = offer.field_evidence.get(field, [])
        if not refs:
            return False
        for ref in refs:
            fragment = allowed.get(ref.fragment_id)
            if (
                fragment is None
                or fragment.legibility != "readable"
                or not ref.quote.strip()
                or ref.quote not in fragment.text
                or (ref.start is None) != (ref.end is None)
            ):
                return False
            if ref.start is not None and (
                ref.end <= ref.start or fragment.text[ref.start : ref.end] != ref.quote
            ):
                return False
        texts[field] = "\n".join(ref.quote for ref in refs)
    name_tokens = set(normalize_text(offer.raw_name).split())
    if not name_tokens <= set(normalize_text(texts["raw_name"]).split()):
        return False
    return True


class AnalysisBudget:
    def __init__(
        self,
        *,
        max_calls=100,
        timeout_seconds=180,
        max_cost=None,
        phase_limits=None,
        phase_deadlines=None,
        clock=None,
    ):
        self.clock = clock or monotonic
        self.deadline = self.clock() + timeout_seconds
        self.max_calls = max_calls
        self.max_cost = max_cost
        self.reserved = 0
        self.calls = {}
        self.lock = Lock()
        self.reason = None
        self.phase_limits = phase_limits or {}
        self.phase_deadlines = phase_deadlines or {}
        self.phase_reserved = {}

    def reserve(self, phase="reading", *, enforce_deadline=True):
        with self.lock:
            if enforce_deadline and self.clock() >= self.deadline:
                self.reason = "deadline_exhausted"
            elif (
                enforce_deadline
                and self.clock() >= self.phase_deadlines.get(phase, self.deadline)
            ):
                self.reason = f"{phase}_deadline_exhausted"
            elif self.reserved >= self.max_calls:
                self.reason = "call_budget_exhausted"
            elif self.phase_reserved.get(phase, 0) >= self.phase_limits.get(phase, self.max_calls):
                self.reason = f"{phase}_call_budget_exhausted"
            elif self.max_cost is not None and (
                any(call.cost is None for call in self.calls.values())
                or sum(call.cost.amount for call in self.calls.values()) >= self.max_cost
            ):
                # Unknown tariff/usage cannot be treated as free calls.
                self.reason = "cost_budget_unverifiable"
            else:
                self.reserved += 1
                self.phase_reserved[phase] = self.phase_reserved.get(phase, 0) + 1
                return True
            return False

    def record(self, call):
        with self.lock:
            previous = self.calls.get(call.call_id)
            if previous is not None and previous != call:
                raise ValueError("Conflicting records for one provider call")
            self.calls[call.call_id] = call

    def usage(self):
        calls = list(self.calls.values())
        if not calls or any(call.usage is None for call in calls):
            return None
        return TokenUsage(
            **{
                name: (
                    sum(getattr(call.usage, name) for call in calls)
                    if all(getattr(call.usage, name) is not None for call in calls)
                    else None
                )
                for name in TokenUsage.model_fields
            }
        )


class PageRecoveryRegistry:
    def __init__(self):
        self.pages = {}
        self.lock = Lock()

    def page(self, number):
        return self.pages.setdefault(number, PageRecoveryRecord(page_number=number))

    def reserve(self, number, budget):
        with self.lock:
            page = self.page(number)
            if page.reread_count or not budget.reserve():
                page.status = "exhausted"
                return False
            page.reread_count = 1
            return True


class StructuredProposalAssembler:
    """Validate A citations per field, preserving unaffected observed proposals."""

    @staticmethod
    def _reference(ref, field, block):
        fragments = block.fragments if ref.scope == "block" else block.page_conditions
        if not 0 <= ref.fragment_index < len(fragments):
            return None
        fragment = fragments[ref.fragment_index]
        if (
            fragment.legibility != "readable"
            or ref.scope == "page_condition"
            and fragment.fragment_id not in block.condition_refs
            or not ref.quote.strip()
        ):
            return None
        if ref.quote not in fragment.text:
            if field != "raw_name":
                return None
            # Recover whitespace-only differences in the declared source. All
            # non-whitespace characters remain exact; ambiguous matches fail.
            pattern = r"\s+".join(re.escape(part) for part in ref.quote.split())
            matches = list(re.finditer(pattern, fragment.text))
            if len(matches) != 1:
                return None
            match = matches[0]
            return EvidenceRef(
                fragment_id=fragment.fragment_id,
                quote=match.group(),
                start=match.start(),
                end=match.end(),
            )
        starts = [m.start() for m in re.finditer(re.escape(ref.quote), fragment.text)]
        if field in {"offer_price", "currency"} and re.search(r"\d", ref.quote):
            # Whole occurrences only: '$1.729' is not '$1.729,00'.
            starts = [
                s
                for s in starts
                if (
                    (s == 0 or fragment.text[s - 1] not in "0123456789.,")
                    and (
                        s + len(ref.quote) == len(fragment.text)
                        or fragment.text[s + len(ref.quote)] not in "0123456789.,"
                    )
                )
            ]
            if len(starts) > 1:
                published = []
                for start in starts:
                    prefix = fragment.text[:start]
                    previous = list(re.finditer(r"\$\s*\d[\d.,]*", prefix))
                    context = prefix[previous[-1].end() :] if previous else prefix
                    if not re.search(
                        r"\b(?:XKG|XLT|por\s+(?:kg|kilo|litro)|antes|anterior|tachado)\s*:?\s*$",
                        context,
                        re.I,
                    ):
                        published.append(start)
                if len(published) == 1:
                    starts = published
            if not starts:
                return None
        unique = len(starts) == 1
        return EvidenceRef(
            fragment_id=fragment.fragment_id,
            quote=ref.quote,
            start=starts[0] if unique else None,
            end=starts[0] + len(ref.quote) if unique else None,
        )

    def assemble_interpretation(self, *, business_id, block, classification, candidates):
        if not business_id.strip():
            raise ValueError("business_id is required")
        offers = []
        for order, proposal in enumerate(block.proposals):
            fields, seen, invalid = {}, set(), set()
            for field in proposal.field_evidence:
                if field.field in seen:
                    invalid.add(field.field)
                seen.add(field.field)
                refs = [self._reference(ref, field.field, block) for ref in field.refs]
                if not refs or any(ref is None for ref in refs):
                    invalid.add(field.field)
                fields[field.field] = [ref for ref in refs if ref is not None]
            minimum, purchase = proposal.minimum_purchase, None
            if minimum is not None:
                refs = [self._reference(ref, "minimum_purchase", block) for ref in minimum.refs]
                if not refs or any(ref is None for ref in refs):
                    invalid.add("minimum_purchase")
                else:
                    purchase = MinimumPurchase(
                        value=minimum.value,
                        unit=minimum.unit,
                        scope=minimum.scope,
                        refs=refs,
                    )
            values = {
                name: getattr(proposal, name)
                for name in (
                    "raw_name",
                    "brand",
                    "unit_size",
                    "unit",
                    "offer_price",
                    "currency",
                    "tax_included",
                    "price_type",
                    "package_quantity",
                    "retail_contents_count",
                )
            }
            # Invalid optional evidence clears that field only. Mandatory name and
            # amount still pass the policy's shared literal/value validators.
            for field in invalid:
                fields.pop(field, None)
                if field in values:
                    values[field] = "unknown" if field == "price_type" else None
            if {"unit", "unit_size"} & invalid:
                values["unit"], values["unit_size"] = None, None
            if "minimum_purchase" in invalid:
                purchase = None
            offers.append(
                OfferInterpretation(
                    offer_id=str(uuid5(NAMESPACE_URL, f"{business_id}:{block.block_id}:{order}")),
                    business_id=business_id,
                    block_id=block.block_id,
                    **values,
                    minimum_purchase=purchase,
                    field_evidence=fields,
                    reason_codes=[f"{field}_invalid_reference" for field in sorted(invalid)],
                )
            )
        return BlockInterpretationResult(block_id=block.block_id, offers=offers)


class MethodBlockAssembler:
    def __init__(self, blocks_assembler):
        self.structured = StructuredProposalAssembler()
        self.blocks = blocks_assembler

    def assemble_interpretation(self, **kwargs):
        assembler = self.structured if kwargs["classification"] is None else self.blocks
        return assembler.assemble_interpretation(**kwargs)

    def supports(self, method):
        target = self.structured if method == AnalysisMethod.OPENAI_STRUCTURED else self.blocks
        return callable(getattr(target, "assemble_interpretation", None))


@contextmanager
def analysis_chunks(path, *, pages=None, chunk_size=2):
    if path.suffix.lower() == ".pdf":
        with pdf_chunks(path, chunk_size=chunk_size, pages=pages) as chunks:
            yield chunks
    else:
        yield [PdfChunk(path, 0, 1, 1, 1)]


class SupplierOfferDocumentAnalysisService:
    """Only ready when the injected components implement the shared interfaces.

    candidate_retriever(business_id=..., offers=..., supplier_name=..., internal_top_k=5) returns
    a mapping of offer IDs to IdentityCandidate DTO lists. question_builder returns
    ChoiceQuestion DTOs; value_detector(block=...) returns ValueCandidate DTOs.
    All DB access occurs in the request worker, never in reader worker threads.
    """

    def __init__(
        self,
        *,
        reader,
        assembler,
        classifier=None,
        policy=None,
        decision_provider=None,
        candidate_retriever=None,
        question_builder=None,
        value_detector=None,
        max_calls=100,
        timeout_seconds=180,
        max_metadata_bytes=4_000_000,
        require_decisions=False,
        chunk_workers=2,
        structured_chunk_size=1,
        clock=None,
    ):
        self.clock = clock or monotonic
        self.reader = reader
        self.assembler = assembler
        self.classifier = classifier
        self.policy = policy
        self.decision_provider = decision_provider
        self.candidate_retriever = candidate_retriever
        self.question_builder = question_builder
        self.value_detector = value_detector
        self.max_calls = max_calls
        self.timeout_seconds = timeout_seconds
        self.max_metadata_bytes = max_metadata_bytes
        self.require_decisions = require_decisions
        if chunk_workers not in {1, 2}:
            raise ValueError("At most two concurrent initial readers are allowed")
        self.chunk_workers = chunk_workers
        if structured_chunk_size not in {1, 2}:
            raise ValueError("Structured reads support one or two pages per request")
        self.structured_chunk_size = structured_chunk_size

    def supports(self, method):
        base = callable(getattr(self.reader, "read", None)) and callable(
            getattr(self.assembler, "assemble_interpretation", None)
        )
        if callable(getattr(self.assembler, "supports", None)):
            base = base and self.assembler.supports(method)
        if self.require_decisions:
            base = (
                base
                and callable(getattr(self.policy, "validate_decision", None))
                and callable(getattr(self.policy, "prepare_request", None))
                and callable(self.candidate_retriever)
            )
        return base and (
            method == AnalysisMethod.OPENAI_STRUCTURED
            or (
                callable(getattr(self.classifier, "classify_block", None))
                and callable(self.question_builder)
                and callable(self.value_detector)
            )
        )

    def analyze(
        self, *, business_id, document_path, method, supplier_hint=None,
        decision_thresholds: DecisionThresholds | None = None,
    ):
        method = AnalysisMethod(method)
        if not business_id.strip():
            raise ValueError("business_id is required")
        if not self.supports(method):
            raise AnalysisUnavailableError("Este mÃ©todo todavÃ­a no estÃ¡ integrado.")
        policy = self.policy
        if decision_thresholds is not None:
            if policy is not None and not isinstance(policy, DocumentDecisionPolicy):
                raise ValueError("Configurable thresholds require DocumentDecisionPolicy")
            policy = DocumentDecisionPolicy(thresholds=decision_thresholds)
        effective_thresholds = getattr(policy, "thresholds", None)
        for provider in (self.classifier, self.decision_provider):
            if hasattr(provider, "acceptance_thresholds"):
                provider.acceptance_thresholds = (
                    asdict(effective_thresholds) if is_dataclass(effective_thresholds) else None
                )
        started = self.clock()
        run_id = uuid4().hex
        ledger = AnalysisLedger(run_id=run_id, business_id=business_id, method=method)

        def log_event(event, **fields):
            # Callers supply operational metadata only, never document/catalog text.
            logger.info(
                "document_analysis %s",
                json.dumps({"event": event, "run_id": run_id, "method": method.value, **fields}),
            )
        if policy:
            thresholds = getattr(policy, "thresholds", None)
            module = sys.modules.get(type(policy).__module__)
            source_path = (
                Path(module.__file__) if module and getattr(module, "__file__", None) else None
            )
            ledger.policy = {
                "implementation": f"{type(policy).__module__}.{type(policy).__name__}",
                "thresholds": asdict(thresholds) if is_dataclass(thresholds) else None,
                "version": getattr(module, "POLICY_VERSION", None),
                "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest()
                if source_path and source_path.is_file()
                else None,
            }
            ledger.policy["fingerprint"] = hashlib.sha256(
                json.dumps(
                    ledger.policy,
                    sort_keys=True,
                    allow_nan=False,
                ).encode()
            ).hexdigest()
        # Independent call/time quotas protect initial coverage from semantic work.
        reading_limit = max(
            1,
            math.ceil(
                self.max_calls * (0.60 if method == AnalysisMethod.OPENAI_STRUCTURED else 0.35)
            ),
        )
        classification_limit = (
            0
            if method == AnalysisMethod.OPENAI_STRUCTURED
            else max(0, math.floor(self.max_calls * 0.40))
        )
        decision_limit = max(0, self.max_calls - reading_limit - classification_limit)
        phase_deadlines = {
            "reading": started + self.timeout_seconds * 0.80,
            "classification": started + self.timeout_seconds * 0.92,
            "decision": started + self.timeout_seconds * 0.98,
        }
        budget = AnalysisBudget(
            max_calls=self.max_calls,
            timeout_seconds=self.timeout_seconds,
            clock=self.clock,
            phase_limits={
                "reading": reading_limit,
                "classification": classification_limit,
                "decision": decision_limit,
            },
            phase_deadlines=phase_deadlines,
        )
        for provider in (self.classifier, self.decision_provider):
            if provider is not None and hasattr(provider, "deadline"):
                provider.deadline = budget.deadline
            if provider is not None and hasattr(provider, "analysis_method"):
                provider.analysis_method = method
        batch_budgets = []
        batch_records = []

        def start_batch(chunks, index):
            nonlocal budget
            batch_started = self.clock()
            phase_deadlines.update({
                "reading": batch_started + self.timeout_seconds * 0.80,
                "classification": batch_started + self.timeout_seconds * 0.92,
                "decision": batch_started + self.timeout_seconds * 0.98,
            })
            budget = AnalysisBudget(
                max_calls=self.max_calls, timeout_seconds=self.timeout_seconds, clock=self.clock,
                phase_limits={
                    "reading": reading_limit, "classification": classification_limit,
                    "decision": decision_limit,
                },
                phase_deadlines=dict(phase_deadlines),
            )
            batch_budgets.append(budget)
            seen_providers = set()
            for provider in (self.classifier, self.decision_provider):
                if provider is None or id(provider) in seen_providers:
                    continue
                seen_providers.add(id(provider))
                if hasattr(provider, "deadline"):
                    provider.deadline = budget.deadline
                begin = getattr(provider, "start_batch", None)
                if callable(begin):
                    begin()
            log_event(
                "batch_started", batch_index=index,
                start_page=chunks[0].start_page, end_page=chunks[-1].end_page,
                chunk_count=len(chunks), timeout_seconds=self.timeout_seconds,
            )
            return batch_started

        registry = PageRecoveryRegistry()
        supplier_name = supplier_hint

        def provider_failure(block, phase, before):
            call = ProviderCallRecord(
                call_id=uuid4().hex,
                run_id=run_id,
                reading_id=block.reading_id,
                provider="jev",
                model="unknown",
                prompt_version="unknown",
                schema_version=WIRE_VERSION,
                analysis_method=method,
                phase=phase,
                start_page=block.page_number,
                end_page=block.page_number,
                duration_seconds=max(0, self.clock() - before),
                status="error",
                safe_error=f"{phase}_provider_exception",
            )
            budget.record(call)
            return call.call_id

        def read_request(chunk, phase):
            return DocumentReadRequest(
                document_path=chunk.path,
                run_id=run_id,
                reading_id=uuid4().hex,
                analysis_method=method,
                phase=phase,
                source_filename=document_path.name,
                start_page=chunk.start_page,
                end_page=chunk.end_page,
                supplier_hint=supplier_hint,
                # Reading deadline limits starts, not an already active request.
                timeout_seconds=max(0.001, phase_deadlines["decision"] - self.clock()),
            )

        def transport_read(request):
            # HTTP/SDK only; ledger, policies and DB stay in the request worker.
            before = self.clock()
            log_event(
                "reader_started", reading_id=request.reading_id, phase=request.phase,
                start_page=request.start_page, end_page=request.end_page,
                timeout_seconds=request.timeout_seconds,
            )
            try:
                return request, before, self.reader.read(request=request), None
            except Exception as exc:
                return request, before, None, type(exc).__name__

        def read(chunk, phase, reserved=False, received=None):
            nonlocal supplier_name
            if received is None and not reserved and not budget.reserve():
                for number in range(chunk.start_page, chunk.end_page + 1):
                    registry.page(number).reason_codes.append(budget.reason)
                return []
            request, before, result, transport_error = received or transport_read(
                read_request(chunk, phase)
            )
            reading_id = request.reading_id
            try:
                if transport_error:
                    raise ValueError("Reader transport failed")
                if (
                    result.call.run_id != run_id
                    or result.call.reading_id != reading_id
                    or result.call.analysis_method != method
                    or result.call.phase != phase
                    or result.call.start_page not in {None, chunk.start_page}
                    or result.call.end_page not in {None, chunk.end_page}
                ):
                    raise ValueError("Reader result belongs to another execution")
                # Legacy/fake readers may omit ranges; the validated request owns
                # this binding and makes attempted-page counters unambiguous.
                result.call.start_page = chunk.start_page
                result.call.end_page = chunk.end_page
                budget.record(result.call)
                extraction = result.extraction
                if extraction is None:
                    raise ValueError("Reader returned no evidence")
                if (method == AnalysisMethod.OPENAI_STRUCTURED) != isinstance(
                    extraction, ReaderStructuredExtractionV2
                ):
                    raise ValueError("Reader returned the wrong wire method")
            except Exception:
                if not any(c.reading_id == reading_id for c in budget.calls.values()):
                    budget.record(
                        ProviderCallRecord(
                            call_id=reading_id,
                            run_id=run_id,
                            reading_id=reading_id,
                            provider="openai",
                            model="unknown",
                            prompt_version="unknown",
                            schema_version=WIRE_VERSION,
                            analysis_method=method,
                            phase=phase,
                            start_page=chunk.start_page,
                            end_page=chunk.end_page,
                            duration_seconds=max(0, self.clock() - before),
                            status="error",
                            safe_error="reader_failed",
                        )
                    )
                for number in range(chunk.start_page, chunk.end_page + 1):
                    registry.page(number).reason_codes.append("reader_failed")
                return []
            supplier_name = supplier_name or extraction.supplier_name
            seen = set()
            blocks = []
            for local_order, page in enumerate(extraction.pages):
                local = page.page_number
                valid = local is not None and 1 <= local <= chunk.page_count and local not in seen
                original = chunk.start_page + local - 1 if valid else None
                if valid:
                    seen.add(local)
                    state = registry.page(original)
                    state.initial_done = True
                    state.reading_ids.append(reading_id)
                    state.status = "recovered" if phase == "reread" else "read"
                    if page.coverage.needs_visual_review:
                        state.reason_codes.append("visual_coverage_incomplete")
                    estimate = page.coverage.estimated_offer_blocks
                    if estimate is not None and estimate > 0 and len(page.blocks) < estimate * 0.7:
                        state.reason_codes.append("low_block_coverage")
                else:
                    ledger.invalid_evidence.append(
                        {
                            "reading_id": reading_id,
                            "reason": "invalid_page_number",
                            "page": page.model_dump(mode="json"),
                        }
                    )
                prefix = f"{run_id}:{reading_id}:p{original or 'unknown'}:{local_order}"
                conditions = [
                    EvidenceFragment(
                        fragment_id=f"{prefix}:c{i}",
                        text=f.text,
                        order=i,
                        legibility=f.legibility,
                    )
                    for i, f in enumerate(page.page_conditions)
                ]
                ledger.page_conditions[prefix] = conditions
                for order, raw in enumerate(page.blocks):
                    block_id = f"{prefix}:b{order}"
                    bad_refs = any(
                        i < 0 or i >= len(conditions) for i in raw.page_condition_indices
                    )
                    block = OfferTextBlock(
                        block_id=block_id,
                        run_id=run_id,
                        reading_id=reading_id,
                        page_number=original,
                        order_on_page=order,
                        full_text="\n".join(f.text for f in raw.fragments),
                        fragments=[
                            EvidenceFragment(
                                fragment_id=f"{block_id}:f{i}",
                                text=f.text,
                                order=i,
                                legibility=f.legibility,
                            )
                            for i, f in enumerate(raw.fragments)
                        ],
                        page_conditions=conditions,
                        condition_refs=[
                            conditions[i].fragment_id
                            for i in raw.page_condition_indices
                            if 0 <= i < len(conditions)
                        ],
                        proposals=getattr(raw, "proposals", []),
                        warnings=raw.warnings,
                        reason_codes=(["invalid_page_number"] if not valid else [])
                        + (["invalid_condition_reference"] if bad_refs else []),
                    )
                    ledger.blocks.append(block)
                    blocks.append(block)
            for local in set(range(1, chunk.page_count + 1)) - seen:
                registry.page(chunk.start_page + local - 1).reason_codes.append(
                    "missing_page_report"
                )
            return blocks

        semantic_queue = deque()
        decided = set()
        candidate_audit = {}
        policy_audit = {}

        def validate_call(call, block, phase):
            if (
                call.run_id != run_id
                or call.reading_id != block.reading_id
                or call.analysis_method != method
                or call.phase != phase
                or call.start_page not in {None, block.page_number}
                or call.end_page not in {None, block.page_number}
            ):
                raise ValueError("Provider call belongs to another evidence/phase")

        def finish_semantic(task, result, error=None, *, transported=False, before=None):
            phase, block, request, audit, candidates = task
            try:
                if error is not None:
                    raise ValueError("Semantic transport failed")
                if phase == "classification":
                    if result.run_id != run_id or result.block_id != block.block_id:
                        raise ValueError("Foreign classification")
                elif result.offer_id != request.offer.offer_id:
                    raise ValueError("Foreign offer decision")
                for call in result.calls:
                    validate_call(call, block, phase)
                for call in result.calls:
                    budget.record(call)
                audit.decisions = result.decisions
                audit.call_ids = [call.call_id for call in result.calls]
                audit.reason_codes = list(result.reason_codes)
                if not result.calls and result.reason_codes:
                    deferred = any(
                        "budget" in code or "deadline" in code for code in result.reason_codes
                    )
                    audit.reason_codes.append(
                        "transport_deferred" if deferred else "local_rejection"
                    )
                audit.status = "partial" if result.reason_codes else "success"
                if phase == "classification":
                    audit.classifications = result.classifications
                    interpret(block, classification=result, candidates=candidates)
                else:
                    assessed = policy.validate_decision(request=request, decision=result)
                    index = next(
                        i
                        for i, o in enumerate(ledger.interpretations)
                        if o.offer_id == request.offer.offer_id
                    )
                    ledger.interpretations[index] = assessed.offer
                    if assessed.route == "needs_visual_review" and block.page_number:
                        registry.page(block.page_number).reason_codes.append(
                            "missing_visual_evidence"
                        )
            except Exception:
                if transported:
                    audit.call_ids = [provider_failure(block, phase, before)]
                else:
                    audit.reason_codes.append("local_rejection")
                reason = f"jev_{phase}_failed"
                audit.reason_codes.append(reason)
                audit.status = "error"
                if phase == "classification":
                    block.reason_codes.append(reason)
                else:
                    offer = next(
                        o for o in ledger.interpretations if o.offer_id == request.offer.offer_id
                    )
                    offer.reason_codes.append(reason)

            log_event(
                "jev_finished", phase=phase, page=block.page_number, block_id=block.block_id,
                status=audit.status, reason_codes=audit.reason_codes,
                call_count=len(audit.call_ids), transported=transported,
            )

        def enqueue_semantic(phase, block, request, candidates=None):
            provider = self.classifier if phase == "classification" else self.decision_provider
            audit = DecisionAuditRecord(
                run_id=run_id,
                reading_id=block.reading_id,
                block_id=block.block_id,
                offer_id=request.offer.offer_id if phase == "decision" else None,
                phase=phase,
                questions=request.questions,
            )
            ledger.decision_audit.append(audit)
            task = (phase, block, request, audit, candidates)
            try:
                preflight = getattr(provider, "preflight", None)
                local = preflight(request=request) if callable(preflight) else None
            except Exception:
                finish_semantic(task, None, error="preflight_failed")
                return
            if local is not None:
                finish_semantic(task, local)
            else:
                semantic_queue.append(task)

        def interpret(block, classification=None, candidates=None):
            if candidates is None:
                if block.reason_codes:
                    return
                candidates = self.value_detector(block=block) if self.value_detector else []
                if method == AnalysisMethod.OPENAI_BLOCKS_JEV:
                    try:
                        questions = self.question_builder(block=block, candidates=candidates)
                        enqueue_semantic(
                            "classification",
                            block,
                            BlockClassificationRequest(
                                business_id=business_id,
                                block=block,
                                candidates=candidates,
                                questions=questions,
                            ),
                            candidates,
                        )
                    except Exception:
                        block.reason_codes.append("classification_preparation_failed")
                    return
            try:
                result = self.assembler.assemble_interpretation(
                    business_id=business_id,
                    block=block,
                    classification=classification,
                    candidates=candidates,
                )
                if result.block_id != block.block_id:
                    raise ValueError("Foreign interpretation")
                block.reason_codes.extend(result.reason_codes)
                if result.needs_visual_review and block.page_number:
                    registry.page(block.page_number).reason_codes.append("missing_visual_evidence")
                for offer in result.offers:
                    if offer.business_id != business_id or offer.block_id != block.block_id:
                        raise ValueError("Foreign offer interpretation")
                    if any(old.offer_id == offer.offer_id for old in ledger.interpretations):
                        raise ValueError("Duplicate offer ID")
                    block.derived_offer_ids.append(offer.offer_id)
                    ledger.interpretations.append(offer)
            except Exception:
                block.reason_codes.append("interpretation_failed")

        def decide_offers():
            batch = [o for o in ledger.interpretations if o.offer_id not in decided]
            if not batch or policy is None or self.candidate_retriever is None:
                return
            validate_metadata_size(
                ledger.model_dump(mode="json"), max_bytes=self.max_metadata_bytes
            )
            found = self.candidate_retriever(
                business_id=business_id,
                offers=batch,
                internal_top_k=5,
                supplier_name=supplier_name,
            )
            blocks_by_id = {b.block_id: b for b in ledger.blocks}
            for index, offer in enumerate(ledger.interpretations):
                if offer.offer_id in decided:
                    continue
                block = blocks_by_id[offer.block_id]
                identity = found.get(offer.offer_id, [])
                if any(c.business_id != business_id for c in identity):
                    raise ValueError("Foreign identity candidate")
                candidate_audit[offer.offer_id] = [c.model_dump(mode="json") for c in identity]
                request = OfferDecisionRequest(
                    business_id=business_id,
                    block=block,
                    offer=offer,
                    candidates=identity,
                    questions=[],
                )
                prepare = getattr(policy, "prepare_request", None)
                if prepare:
                    request = prepare(request=request)
                assessed = policy.validate_decision(request=request, decision=None)
                request.offer = assessed.offer
                ledger.interpretations[index] = assessed.offer
                decided.add(offer.offer_id)
                if assessed.route == "needs_jev" and self.decision_provider and request.questions:
                    enqueue_semantic("decision", block, request)
                else:
                    policy_audit[offer.offer_id] = {
                        "route": assessed.route,
                        "identity_status": assessed.offer.identity_status,
                        "product_id": assessed.offer.product_id,
                        "price_status": assessed.offer.price_status,
                        "price_type": assessed.offer.price_type,
                        "reason_codes": assessed.reason_codes,
                    }
                if assessed.route == "needs_visual_review" and block.page_number:
                    registry.page(block.page_number).reason_codes.append("missing_visual_evidence")

        def transport_semantic(task):
            phase, _, request, _, _ = task
            provider = self.classifier if phase == "classification" else self.decision_provider
            operation = (
                provider.classify_block if phase == "classification" else provider.decide_offer
            )
            before = self.clock()
            attempts = 0

            def reserve_call():
                nonlocal attempts
                accepted = budget.reserve(phase)
                attempts += int(accepted)
                return accepted

            try:
                kwargs = {"request": request}
                if getattr(provider, "supports_call_reservation", False):
                    kwargs["reserve_call"] = reserve_call
                else:
                    attempts = 1  # Legacy single-request provider, reserved by scheduler.
                return operation(**kwargs), None, before, attempts
            except Exception as exc:
                return None, type(exc).__name__, before, attempts

        def defer(task, reason):
            phase, block, request, audit, _ = task
            audit.status = "partial"
            audit.reason_codes = [reason, "transport_deferred"]
            log_event(
                "jev_deferred", phase=phase, page=block.page_number,
                block_id=block.block_id, reason=reason,
                elapsed_seconds=round(self.clock() - started, 3),
            )
            if phase == "classification":
                block.reason_codes.append(reason)
            else:
                next(
                    o for o in ledger.interpretations if o.offer_id == request.offer.offer_id
                ).reason_codes.append(reason)

        def run_pipeline(chunks, phase="initial", block_callback=None):
            reading = deque(chunks)
            # Independent bounded transports: two OpenAI readers, one textual JEV.
            # No Future timeout releases a real slot; both executors join before
            # the caller deletes chunk PDFs. Ledger/catalog remain on this worker.
            with (
                ThreadPoolExecutor(max_workers=self.chunk_workers) as reader_executor,
                ThreadPoolExecutor(max_workers=1) as semantic_executor,
            ):
                pending = {}
                while reading or semantic_queue or pending:
                    while reading or semantic_queue:
                        reading_active = sum(kind == "read" for kind, _ in pending.values())
                        semantic_active = len(pending) - reading_active
                        if reading and reading_active < self.chunk_workers:
                            chunk = reading.popleft()
                            # Every initial chunk of this bounded batch must be
                            # attempted, even if an earlier call already timed out.
                            # Recovery and JEV still honor their phase deadlines.
                            if not budget.reserve("reading", enforce_deadline=phase != "initial"):
                                log_event(
                                    "reading_stopped", phase=phase, reason=budget.reason,
                                    remaining_page_ranges=[
                                        [c.start_page, c.end_page] for c in [chunk, *reading]
                                    ],
                                    elapsed_seconds=round(self.clock() - started, 3),
                                    reading_start_deadline_seconds=self.timeout_seconds * 0.80,
                                    reserved_calls=dict(budget.phase_reserved),
                                )
                                for remaining in [chunk, *reading]:
                                    for number in range(
                                        remaining.start_page, remaining.end_page + 1
                                    ):
                                        registry.page(number).reason_codes.append(budget.reason)
                                reading.clear()
                                continue
                            future = reader_executor.submit(
                                transport_read, read_request(chunk, phase)
                            )
                            pending[future] = ("read", chunk)
                        elif semantic_queue and semantic_active < 1:
                            task = semantic_queue.popleft()
                            kind, _, _, _, _ = task
                            if self.clock() >= phase_deadlines[kind]:
                                defer(task, f"{kind}_deadline_exhausted")
                                continue
                            provider = (
                                self.classifier
                                if kind == "classification"
                                else self.decision_provider
                            )
                            if not getattr(
                                provider, "supports_call_reservation", False
                            ) and not budget.reserve(kind):
                                defer(task, budget.reason)
                                continue
                            future = semantic_executor.submit(transport_semantic, task)
                            log_event(
                                "jev_scheduled", phase=kind, page=task[1].page_number,
                                block_id=task[1].block_id, queued_tasks=len(semantic_queue),
                            )
                            pending[future] = ("semantic", task)
                        else:
                            break
                    if not pending:
                        continue
                    completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in completed:
                        kind, payload = pending.pop(future)
                        if kind == "read":
                            received = future.result()
                            blocks = read(payload, phase, received=received)
                            call = received[2].call if received[2] is not None else None
                            log_event(
                                "reader_finished", phase=phase,
                                start_page=payload.start_page, end_page=payload.end_page,
                                status=call.status if call else "error",
                                safe_error=call.safe_error if call else received[3],
                                duration_seconds=round(self.clock() - received[1], 3),
                                block_count=len(blocks),
                                elapsed_seconds=round(self.clock() - started, 3),
                            )
                            for block in blocks:
                                if block_callback:
                                    block_callback(block)
                                interpret(block)
                        else:
                            result, error, before, attempts = future.result()
                            finish_semantic(
                                payload, result, error, transported=bool(attempts), before=before
                            )
                    decide_offers()

        chunk_size = self.structured_chunk_size if method == AnalysisMethod.OPENAI_STRUCTURED else 2
        with analysis_chunks(document_path, chunk_size=chunk_size) as chunks:
            page_count = chunks[-1].end_page
            for number in range(1, page_count + 1):
                registry.page(number)
            log_event(
                "started", page_count=page_count, chunk_count=len(chunks), chunk_size=chunk_size,
                budget_scope="batch",
                reader_workers=self.chunk_workers, processing_timeout_seconds=self.timeout_seconds,
                reader_transport_timeout_seconds=getattr(self.reader, "_timeout_seconds", None),
                phase_call_limits=budget.phase_limits,
                phase_start_deadline_seconds={
                    name: deadline - started for name, deadline in phase_deadlines.items()
                },
            )
            # One batch fits all reader slots and its reading call quota. Slow
            # active requests cannot prevent later batches from being submitted.
            # Drain all transports before renewing budgets/provider deadlines.
            batch_size = min(self.chunk_workers, reading_limit)
            for offset in range(0, len(chunks), batch_size):
                batch = chunks[offset:offset + batch_size]
                index = len(batch_records) + 1
                batch_started = start_batch(batch, index)
                run_pipeline(batch)
                record = {
                    "batch_index": index,
                    "start_page": batch[0].start_page,
                    "end_page": batch[-1].end_page,
                    "duration_seconds": max(0, self.clock() - batch_started),
                    "phase_reserved_calls": dict(budget.phase_reserved),
                    "stop_reason": budget.reason,
                }
                batch_records.append(record)
                log_event("batch_finished", **record)
        # Retry only visual failures, never local semantic rejection/provider doubts.
        visual_reasons = {
            "reader_failed",
            "missing_page_report",
            "visual_coverage_incomplete",
            "low_block_coverage",
            "missing_visual_evidence",
        }
        for number, state in sorted(registry.pages.items()):
            if not visual_reasons.intersection(state.reason_codes):
                continue
            if state.reread_count or self.clock() >= phase_deadlines["reading"]:
                state.status = "exhausted"
                continue
            # The pipeline reserves the actual reread; do not reserve it twice.
            if budget.phase_reserved.get("reading", 0) >= reading_limit:
                state.status = "exhausted"
                continue
            state.reread_count = 1
            with analysis_chunks(document_path, pages=[number]) as chunks:
                before_blocks = [b for b in ledger.blocks if b.page_number == number]
                old_reasons = list(state.reason_codes)
                state.attempt_reasons["initial"] = old_reasons
                state.reason_codes = []

                def associate(block, before_blocks=tuple(before_blocks)):
                    matches = [
                        b
                        for b in before_blocks
                        if b.full_text == block.full_text
                        and [f.text for f in b.page_conditions]
                        == [f.text for f in block.page_conditions]
                    ]
                    repeated = sum(
                        b.full_text == block.full_text
                        for b in ledger.blocks
                        if b.reading_id == block.reading_id
                    )
                    if len(matches) == 1 and repeated == 1:
                        block.supersedes_block_id = matches[0].block_id
                    else:
                        block.reason_codes.append("unverified_reading_association")

                run_pipeline(chunks, "reread", associate)
                state.attempt_reasons["reread"] = list(state.reason_codes)
                if not any(
                    b.reading_id in state.reading_ids and b not in before_blocks
                    for b in ledger.blocks
                    if b.page_number == number
                ):
                    state.reason_codes = list(dict.fromkeys(state.reason_codes + old_reasons))
        # Completion order is transport timing, not the visual order of the source.
        ledger.blocks.sort(key=lambda b: (b.page_number or page_count + 1, b.order_on_page))

        def usable(offer, block):
            return (
                bool(offer.raw_name)
                and offer.offer_price is not None
                and offer.offer_price.is_finite()
                and offer.offer_price > 0
                and (not self.require_decisions or usable_evidence(offer, block))
            )

        def compatible(previous, incoming):
            for name in ("raw_name", "brand", "unit_size", "unit", "offer_price", "currency"):
                value = getattr(previous, name)
                if value is not None and value != getattr(incoming, name):
                    return False
            if previous.price_status == "confirmed" and (
                incoming.price_status != "confirmed"
                or previous.price_type != incoming.price_type
                or previous.package_quantity != incoming.package_quantity
            ):
                return False
            if previous.identity_status == "confirmed" and (
                incoming.identity_status != "confirmed"
                or previous.product_id != incoming.product_id
            ):
                return False
            return previous.identity_status != "no_match" or incoming.identity_status == "no_match"

        superseded = set()
        block_map = {b.block_id: b for b in ledger.blocks}
        for block in ledger.blocks:
            if not block.supersedes_block_id or block.reason_codes:
                continue
            previous = block_map[block.supersedes_block_id]
            old_offers = [o for o in ledger.interpretations if o.block_id == previous.block_id]
            new_offers = [o for o in ledger.interpretations if o.block_id == block.block_id]
            available = list(new_offers)
            covered = True
            # Most constrained versions first; an incoming variant can cover only one old variant.
            ordered = sorted(
                old_offers,
                key=lambda o: (
                    o.identity_status == "confirmed",
                    o.price_status == "confirmed",
                    sum(
                        getattr(o, name) is not None
                        for name in (
                            "raw_name",
                            "brand",
                            "unit_size",
                            "unit",
                            "offer_price",
                            "currency",
                        )
                    ),
                ),
                reverse=True,
            )
            for old in ordered:
                match = next(
                    (
                        n
                        for n in available
                        if compatible(old, n) and (not usable(old, previous) or usable(n, block))
                    ),
                    None,
                )
                if match is None:
                    covered = False
                    break
                available.remove(match)
            if covered and any(usable(o, block) for o in new_offers) and not previous.reason_codes:
                superseded.add(previous.block_id)
            else:
                # Keep prior canonical offers; this weaker version remains visible in audit.
                block.reason_codes.append("reread_quality_regression")
        current = [b for b in ledger.blocks if b.block_id not in superseded]
        current_ids = {b.block_id for b in current}
        offers = [
            o
            for o in ledger.interpretations
            if o.block_id in current_ids
            and "reread_quality_regression" not in block_map[o.block_id].reason_codes
        ]
        offers.sort(
            key=lambda o: (
                block_map[o.block_id].page_number or page_count + 1,
                block_map[o.block_id].order_on_page,
            )
        )
        final = []
        for offer in offers:
            if not offer.raw_name or not offer.raw_name.strip() or offer.offer_price is None:
                continue
            if not offer.offer_price.is_finite() or offer.offer_price <= 0:
                continue
            block = next(b for b in current if b.block_id == offer.block_id)
            if self.require_decisions and not usable_evidence(offer, block):
                block.reason_codes.append("published_fields_evidence_unverified")
                continue
            # Unknown base stays unknown: confidence never supplies commercial quantity.
            price_type = offer.price_type if offer.price_status == "confirmed" else "unknown"
            quantity = offer.package_quantity if price_type == "pack" else None
            if price_type == "pack" and (quantity is None or not 2 <= quantity <= 1000):
                price_type, quantity = "unknown", None
            item = ExtractedSupplierOfferItem(
                raw_name=offer.raw_name,
                brand=offer.brand,
                unit_size=offer.unit_size,
                unit=offer.unit,
                offer_price=offer.offer_price,
                currency=offer.currency or "ARS",
                tax_included=offer.tax_included,
                price_type=price_type,
                package_quantity=quantity,
                page_number=block.page_number,
                selected_source="analysis_v2",
                provider_observations={
                    "analysis_v2": {
                        "offer_id": offer.offer_id,
                        "block_id": block.block_id,
                        "interpretation": offer.model_dump(mode="json"),
                    },
                },
            )
            final.append(item)
        usable_ids = {i.provider_observations["analysis_v2"]["offer_id"] for i in final}
        for block in current:
            missing = set(block.derived_offer_ids) - usable_ids
            reasons = list(
                dict.fromkeys(
                    block.reason_codes
                    + (["missing_usable_offer"] if not block.derived_offer_ids or missing else [])
                )
            )
            if reasons:
                ledger.pending_blocks.append(
                    PendingBlock(
                        block_id=block.block_id,
                        page_number=block.page_number,
                        order_on_page=block.order_on_page,
                        text=block.full_text,
                        reason_codes=reasons,
                        reason="La oferta necesita revisiÃ³n de los datos.",
                    )
                )
        review_pages = sorted(
            n for n, p in registry.pages.items() if p.reason_codes or not p.initial_done
        )
        ledger.pages = list(registry.pages.values())
        # Aggregate every batch without losing usage or earlier failures. The
        # final budget also contains any allowed recovery calls after the batches.
        all_calls = {key: call for batch in batch_budgets for key, call in batch.calls.items()}
        total_reserved = {}
        for batch in batch_budgets:
            for phase, count in batch.phase_reserved.items():
                total_reserved[phase] = total_reserved.get(phase, 0) + count
        batch_stop_reasons = [batch.reason for batch in batch_budgets if batch.reason]
        budget.calls = all_calls
        ledger.calls = list(all_calls.values())
        attempted_pages = {
            number
            for call in ledger.calls
            if call.provider == "openai"
            for number in range(call.start_page or 1, (call.end_page or call.start_page or 1) + 1)
        }
        read_pages = {n for n, state in registry.pages.items() if state.initial_done}
        failed_pages = sorted(attempted_pages - read_pages)
        unattempted_pages = sorted(set(registry.pages) - attempted_pages)
        local_rejections = sum(
            "local_rejection" in audit.reason_codes for audit in ledger.decision_audit
        )
        summary = AnalysisSummary(
            run_id=run_id,
            method=method,
            status="partial"
            if ledger.pending_blocks
            or review_pages
            or ledger.invalid_evidence
            or batch_stop_reasons
            or any(o.identity_status == "pending" or o.price_status == "pending" for o in offers)
            else "completed",
            page_count=page_count,
            read_page_count=len(read_pages),
            attempted_page_count=len(attempted_pages),
            unattempted_page_numbers=unattempted_pages,
            failed_page_numbers=failed_pages,
            provider_call_count=len(ledger.calls),
            local_rejection_count=local_rejections,
            observed_block_count=len(current),
            interpreted_offer_count=len(final),
            pending_block_count=len(ledger.pending_blocks),
            unlinked_offer_count=sum(
                o.product_id is None for o in offers if o.offer_id in usable_ids
            ),
            identity_review_offer_count=sum(
                o.identity_status == "pending" for o in offers if o.offer_id in usable_ids
            ),
            price_review_offer_count=sum(
                o.price_status == "pending" for o in offers if o.offer_id in usable_ids
            ),
            review_page_numbers=review_pages,
            duration_seconds=max(0, self.clock() - started),
            usage=budget.usage(),
            cost=None,
        )
        metadata = {
            "analysis": summary.model_dump(mode="json"),
            "analysis_ledger": ledger.model_dump(mode="json"),
            "needs_review_pages": review_pages,
            "analysis_v2": True,
            "analysis_execution": {
                "version": "bounded_batch_pipeline_v2",
                "source_sha256": hashlib.sha256(document_path.read_bytes()).hexdigest()
                if document_path.is_file()
                else None,
                "phase_call_limits": budget.phase_limits,
                "phase_reserved_calls": total_reserved,
                "budget_scope": "batch",
                "batch_count": len(batch_records),
                "batch_max_reader_requests": min(self.chunk_workers, reading_limit),
                "batches": batch_records,
                "phase_start_deadline_fractions": {
                    "reading": 0.80,
                    "classification": 0.92,
                    "decision": 0.98,
                },
                "processing_timeout_seconds": self.timeout_seconds,
                "reader_transport_timeout_seconds": getattr(self.reader, "_timeout_seconds", None),
                "reader_concurrency_limit": self.chunk_workers,
                "jev_concurrency_limit": 1,
                "reader_chunk_size": chunk_size,
                "identity_candidates": candidate_audit,
                "policy_assessments": policy_audit,
                "unknown_usage_call_ids": [c.call_id for c in ledger.calls if c.usage is None],
                "known_usage": {
                    field: sum(
                        getattr(c.usage, field)
                        for c in ledger.calls
                        if c.usage and getattr(c.usage, field) is not None
                    )
                    if any(c.usage and getattr(c.usage, field) is not None for c in ledger.calls)
                    else None
                    for field in TokenUsage.model_fields
                },
            },
            "recovery_pending": {
                "continuation_available": False,
                "reason": "continuation_not_implemented",
                "page_numbers": sorted(set(registry.pages) - read_pages),
                "classification_block_ids": list(
                    dict.fromkeys(
                        audit.block_id
                        for audit in ledger.decision_audit
                        if audit.phase == "classification" and audit.status != "success"
                    )
                ),
                "offer_ids": list(
                    dict.fromkeys(
                        audit.offer_id
                        for audit in ledger.decision_audit
                        if audit.offer_id
                        and any(
                            "budget" in code or "deadline" in code or "failed" in code
                            for code in audit.reason_codes
                        )
                    )
                ),
            },
        }
        validate_metadata_size(metadata, max_bytes=self.max_metadata_bytes)
        log_event(
            "finished", status=summary.status, duration_seconds=summary.duration_seconds,
            page_count=page_count, attempted_page_count=summary.attempted_page_count,
            read_page_count=summary.read_page_count,
            unattempted_pages=summary.unattempted_page_numbers,
            failed_pages=summary.failed_page_numbers, offer_count=len(final),
            pending_block_count=summary.pending_block_count, call_count=len(ledger.calls),
        )
        return SupplierOfferExtraction(
            supplier_name=supplier_name,
            source_filename=document_path.name,
            items=final,
            document_metadata=metadata,
            warnings=["El anÃ¡lisis conserva datos pendientes de revisiÃ³n."]
            if summary.status == "partial"
            else [],
        )
