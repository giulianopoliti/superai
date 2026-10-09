import logging
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import uuid4

from app.modules.procurement.schemas import ExtractedSupplierOfferItem, SupplierOfferExtraction
from app.providers.documents.base import DocumentExtractionProvider, DocumentPageContext
from app.providers.documents.chunking import pdf_chunks
from app.providers.documents.errors import provider_error
from app.providers.documents.fallback import DocumentExtractionUnavailableError
from app.providers.documents.reconciliation import (
    OfferReconciler,
    agreement_score,
    completeness,
    conflicting_fields,
    observation,
    reconcile_items,
)

logger = logging.getLogger("uvicorn.error")


@dataclass
class _RequestMetrics:
    lock: Lock = field(default_factory=Lock)
    active: int = 0
    peak: int = 0
    initial: int = 0
    retry: int = 0
    disabled: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

    def enter(self, retry: bool):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            if retry:
                self.retry += 1
            else:
                self.initial += 1

    def leave(self):
        with self.lock:
            self.active -= 1


class ChunkedDocumentExtractionProvider:
    """Bounded PDF blocks and sparse rereads with one or two readers."""

    def __init__(
        self,
        providers: list[tuple[str, DocumentExtractionProvider]],
        *,
        chunk_size: int = 6,
        chunk_workers: int = 2,
        retry_workers: int = 2,
        timeout_seconds: float = 60,
        retry_low_coverage: bool = True,
        jev: OfferReconciler | None = None,
    ) -> None:
        if not 1 <= len(providers) <= 2 or len({name for name, _ in providers}) != len(providers):
            raise ValueError("Extraction requires one or two distinct providers.")
        if not 1 <= chunk_size <= 6 or not 1 <= chunk_workers <= 2 or not 1 <= retry_workers <= 2:
            raise ValueError("At most six pages and two concurrent chunks/pages are allowed.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._providers = providers
        self._chunk_size = chunk_size
        self._chunk_workers = chunk_workers
        self._retry_workers = retry_workers
        self._timeout_seconds = timeout_seconds
        self._retry_low_coverage = retry_low_coverage
        self._jev = jev

    def extract_supplier_offer(
        self,
        *,
        document_path: Path,
        supplier_hint: str | None = None,
    ) -> SupplierOfferExtraction:
        started = monotonic()
        extraction_id = uuid4().hex[:12]
        metrics = _RequestMetrics()
        logger.info(
            "document_extraction extraction_id=%s stage=started file_type=%s",
            extraction_id,
            document_path.suffix.lower() or "unknown",
        )
        if document_path.suffix.lower() == ".pdf":
            extraction = self._extract_pdf(
                document_path, supplier_hint, metrics, extraction_id=extraction_id
            )
        else:
            results, failures = self._read_with_all(
                document_path, supplier_hint, metrics, extraction_id=extraction_id
            )
            if not results:
                raise DocumentExtractionUnavailableError(
                    "Ningún lector pudo procesar el documento."
                )
            extraction = self._resolve_results(results, failures, document_path.name, supplier_hint)
        extraction.document_metadata.update(
            {
                "extraction_id": extraction_id,
                "peak_active_requests": metrics.peak,
                "initial_provider_calls": metrics.initial,
                "retry_provider_calls": metrics.retry,
                "duration_seconds": round(monotonic() - started, 3),
                "provider_errors": metrics.errors,
                "disabled_providers": metrics.disabled,
            }
        )
        logger.info(
            "document_extraction extraction_id=%s stage=completed duration_ms=%.1f "
            "item_count=%s initial_calls=%s retry_calls=%s failures=%s",
            extraction_id,
            (monotonic() - started) * 1000,
            len(extraction.items),
            metrics.initial,
            metrics.retry,
            len(metrics.errors),
        )
        return extraction

    def _read_with_all(
        self,
        document_path: Path,
        supplier_hint: str | None,
        metrics: _RequestMetrics,
        context: DocumentPageContext | None = None,
        retry: bool = False,
        extraction_id: str = "unknown",
    ) -> tuple[dict[str, SupplierOfferExtraction], dict[str, str]]:
        results, failures = {}, {}

        def read(name, provider):
            with metrics.lock:
                if name in metrics.disabled:
                    return None
            metrics.enter(retry)
            provider_started_at = monotonic()
            page_range = (
                f"{context.start_page}-{context.end_page}" if context is not None else "all"
            )
            logger.info(
                "document_extraction extraction_id=%s stage=provider_started provider=%s "
                "phase=%s pages=%s",
                extraction_id,
                name,
                "retry" if retry else "initial",
                page_range,
            )
            try:
                kwargs = {"document_path": document_path, "supplier_hint": supplier_hint}
                if context is not None:
                    kwargs["page_context"] = context
                result = provider.extract_supplier_offer(**kwargs).model_copy(deep=True)
                logger.info(
                    "document_extraction extraction_id=%s stage=provider_completed provider=%s "
                    "phase=%s pages=%s duration_ms=%.1f item_count=%s",
                    extraction_id,
                    name,
                    "retry" if retry else "initial",
                    page_range,
                    (monotonic() - provider_started_at) * 1000,
                    len(result.items),
                )
                return self._map_pages(result, context, name) if context else result
            except Exception as exc:
                logger.warning(
                    "document_extraction extraction_id=%s stage=provider_failed provider=%s "
                    "phase=%s pages=%s duration_ms=%.1f error_type=%s",
                    extraction_id,
                    name,
                    "retry" if retry else "initial",
                    page_range,
                    (monotonic() - provider_started_at) * 1000,
                    type(exc).__name__,
                )
                raise
            finally:
                metrics.leave()

        # SDK timeouts/retries finish inside the slot. A timed-out Future must never
        # launch replacement work while its underlying HTTP request is still running.
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(read, name, provider): name for name, provider in self._providers
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                    if result is None:
                        failures[name] = "disabled after non-retryable error"
                    else:
                        results[name] = result
                except Exception as exc:
                    failures[name] = self._safe_error(exc)
                    detail = provider_error(exc)
                    with metrics.lock:
                        metrics.errors.append({"provider": name, "retry": retry, **detail})
                        if not detail["retryable"]:
                            metrics.disabled[name] = detail
        return {name: results[name] for name in sorted(results)}, failures

    @staticmethod
    def _map_pages(result, context, name):
        invalid = False
        for item in result.items:
            item.provider_observations = {name: observation(item, name)}
            item.page_number = context.original_page(item.page_number)
            invalid |= item.page_number is None
            if item.page_number is None:
                item.agreement_status = "ambiguous"
                item.resolved_by = "human_review"
        valid_pages, seen = [], set()
        for page in result.page_extractions:
            original = context.original_page(page.page_number)
            if original is None or original in seen:
                invalid = True
                continue
            seen.add(original)
            page.page_number = original
            # Flattened items are the source of truth; don't map shared objects twice.
            page.items = [item for item in result.items if item.page_number == original]
            page.coverage.extracted_item_count = len(page.items)
            valid_pages.append(page)
        result.page_extractions = valid_pages
        missing = set(range(context.start_page, context.end_page + 1)) - seen
        result.document_metadata["invalid_page_numbers"] = invalid
        result.document_metadata["missing_page_reports"] = sorted(missing)
        if invalid:
            result.warnings.append("Numeración local ausente o inválida; requiere revisión.")
        result.source_filename = context.source_filename
        return result

    def _run_chunks(
        self,
        chunks,
        workers,
        source_filename,
        supplier_hint,
        metrics,
        retry=False,
        extraction_id="unknown",
    ):
        def process(chunk):
            context = DocumentPageContext(
                source_filename=source_filename,
                start_page=chunk.start_page,
                end_page=chunk.end_page,
            )
            results, failures = self._read_with_all(
                chunk.path,
                supplier_hint,
                metrics,
                context,
                retry,
                extraction_id,
            )
            return chunk, results, failures

        completed = []
        # Keep only `workers` submitted tasks, rather than enqueuing the entire PDF.
        with ThreadPoolExecutor(max_workers=workers) as executor:
            iterator = iter(chunks)
            pending = {executor.submit(process, chunk) for chunk in list(chunks)[:workers]}
            for _ in range(min(workers, len(chunks))):
                next(iterator)
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    completed.append(future.result())
                    chunk = next(iterator, None)
                    if chunk is not None:
                        pending.add(executor.submit(process, chunk))
        return sorted(completed, key=lambda entry: entry[0].start_page)

    def _suspicious_pages(self, chunk, results, failures, unresolved):
        reasons, low_coverage = defaultdict(set), set()
        for page in range(chunk.start_page, chunk.end_page + 1):
            counts = [
                sum(item.page_number == page for item in results[name].items)
                if name in results
                else 0
                for name, _ in self._providers
            ]
            if not any(counts):
                reasons[page].add("empty")
            elif not all(counts):
                reasons[page].add("single_reader_empty")
            if failures:
                reasons[page].add("reader_failure")
            for result in results.values():
                if result.document_metadata.get("invalid_page_numbers"):
                    reasons[page].add("invalid_page_numbers")
                if page in result.document_metadata.get("missing_page_reports", []):
                    reasons[page].add("missing_page_report")
                for report in result.page_extractions:
                    if report.page_number != page:
                        continue
                    coverage = report.coverage
                    estimated = coverage.estimated_offer_blocks
                    low = (
                        estimated is not None
                        and estimated > 0
                        and len(report.items) < estimated * 0.7
                    )
                    if low:
                        low_coverage.add(page)
                        if self._retry_low_coverage:
                            reasons[page].add("low_coverage")
                    if coverage.needs_review or report.warnings:
                        reasons[page].add("reader_warning")
            conflicts = sum(item["page_number"] == page for item in unresolved)
            if conflicts >= 3 or (conflicts and conflicts / max(1, max(counts)) >= 0.25):
                reasons[page].add("conflicts")
        return dict(reasons), low_coverage

    @staticmethod
    def _supplement(previous, incoming):
        """Union a reread with recovered offers; never remove an earlier valid price."""
        combined = [item.model_copy(deep=True) for item in previous]
        for item in incoming:
            duplicate = next(
                (
                    index
                    for index, old in enumerate(combined)
                    if old.page_number == item.page_number
                    and agreement_score(old, item) >= Decimal("0.92")
                    and not conflicting_fields(old, item)
                ),
                None,
            )
            if duplicate is None:
                combined.append(item.model_copy(deep=True))
            elif (item.agreement_status.startswith("agreed_"), completeness(item)) > (
                combined[duplicate].agreement_status.startswith("agreed_"),
                completeness(combined[duplicate]),
            ):
                combined[duplicate] = item.model_copy(deep=True)
        return combined

    @staticmethod
    def _reader_reread(previous, incoming):
        """Use the latest reading of an identity; original attempts remain auditable.

        Earlier canonical offers are restored after reconciliation if absent, so a
        conflicting reread cannot silently discard a recovered price.
        """
        combined = [item.model_copy(deep=True) for item in previous]
        for item in incoming:
            positions = [
                index
                for index, old in enumerate(combined)
                if item.page_number is not None
                and old.page_number == item.page_number
                and agreement_score(old, item) >= Decimal("0.92")
            ]
            if len(positions) == 1:
                combined[positions[0]] = item.model_copy(deep=True)
            else:
                combined.append(item.model_copy(deep=True))
        return combined

    def _extract_pdf(self, document_path, supplier_hint, metrics, *, extraction_id="unknown"):
        with pdf_chunks(document_path, self._chunk_size) as chunks:
            page_count = chunks[-1].end_page
            chunk_count = len(chunks)
            initial = self._run_chunks(
                chunks,
                self._chunk_workers,
                document_path.name,
                supplier_hint,
                metrics,
                extraction_id=extraction_id,
            )
        observations = {name: [] for name, _ in self._providers}
        attempts, warnings, unresolved = [], [], []
        failed_pages, failure_reasons = defaultdict(set), defaultdict(dict)
        retry_reasons, low_coverage = {}, set()
        initial_valid = []
        supplier_name = supplier_hint
        for chunk, results, failures in initial:
            # Shadow calls happen only at final reconciliation, once per ambiguous pair.
            resolved = self._resolve_results(
                results, failures, document_path.name, supplier_hint, shadow=False
            )
            initial_valid.extend(resolved.items)
            suspects, low = self._suspicious_pages(
                chunk,
                results,
                failures,
                resolved.document_metadata["unresolved_offers"],
            )
            retry_reasons.update(suspects)
            low_coverage.update(low)
            for name, result in results.items():
                observations[name].extend(result.items)
                supplier_name = supplier_name or result.supplier_name
                warnings.extend(f"{name}: {warning}" for warning in result.warnings)
            self._record_attempt(
                attempts, failed_pages, failure_reasons, chunk, results, failures, "initial"
            )
        needs_review = set() if self._retry_low_coverage else set(low_coverage)
        # A broken schema/key cannot be repaired by rereading each page. Keep the
        # missing independent verification visible without rereading healthy pages.
        for chunk, results, failures in initial:
            if failures and all(name in metrics.disabled for name in failures):
                for page in range(chunk.start_page, chunk.end_page + 1):
                    needs_review.add(page)
                    if any(item.page_number == page for r in results.values() for item in r.items):
                        reasons = retry_reasons.get(page, set())
                        reasons -= {"reader_failure", "single_reader_empty"}
                        if not reasons:
                            retry_reasons.pop(page, None)
        retried_pages = sorted(retry_reasons)
        if retried_pages:
            with pdf_chunks(document_path, pages=retried_pages) as chunks:
                retries = self._run_chunks(
                    chunks,
                    self._retry_workers,
                    document_path.name,
                    supplier_hint,
                    metrics,
                    retry=True,
                    extraction_id=extraction_id,
                )
            for chunk, results, failures in retries:
                resolved = self._resolve_results(
                    results, failures, document_path.name, supplier_hint, shadow=False
                )
                suspects, low = self._suspicious_pages(
                    chunk,
                    results,
                    failures,
                    resolved.document_metadata["unresolved_offers"],
                )
                needs_review.update(suspects)
                low_coverage.update(low)
                for name, result in results.items():
                    observations[name] = self._reader_reread(observations[name], result.items)
                    supplier_name = supplier_name or result.supplier_name
                    warnings.extend(
                        f"Relectura {chunk.start_page}, {name}: {warning}"
                        for warning in result.warnings
                    )
                self._record_attempt(
                    attempts, failed_pages, failure_reasons, chunk, results, failures, "retry"
                )
        items, unresolved = reconcile_items(observations, self._jev)
        items = self._supplement(items, initial_valid)
        # Keep an earlier usable price, but don't present it as confirmed if a reread
        # introduced contradictory evidence. The originals remain in the review ledger.
        for item in items:
            for conflict in unresolved:
                if conflict["page_number"] != item.page_number:
                    continue
                if any(
                    agreement_score(item, ExtractedSupplierOfferItem.model_validate(raw))
                    >= Decimal("0.92")
                    for raw in conflict["provider_observations"].values()
                ):
                    item.agreement_status = conflict["agreement_status"]
                    item.resolved_by = "human_review"
                    item.agreement_score = Decimal(str(conflict["agreement_score"]))
                    break
        items.sort(key=lambda item: (item.page_number or page_count + 1, item.raw_name))
        needs_review.update(entry["page_number"] for entry in unresolved if entry["page_number"])
        if any(item.page_number is None for item in items):
            needs_review.update(
                page for page, reasons in retry_reasons.items() if "invalid_page_numbers" in reasons
            )
        if needs_review:
            warnings.append(
                "Revisar manualmente las páginas: " + ", ".join(map(str, sorted(needs_review)))
            )
        metadata = self._agreement_metrics(items, unresolved)
        metadata.update(
            {
                "extraction_mode": (
                    "single_source_chunks"
                    if len(self._providers) == 1
                    else "dual_independent_chunks"
                ),
                "page_count": page_count,
                "chunk_size": self._chunk_size,
                "chunk_count": chunk_count,
                "retried_pages": retried_pages,
                "retry_reasons": {
                    str(page): sorted(reasons) for page, reasons in retry_reasons.items()
                },
                "items_by_page": {
                    str(page): sum(item.page_number == page for item in items)
                    for page in range(1, page_count + 1)
                },
                "items_by_provider": {name: len(items) for name, items in observations.items()},
                "failed_pages_by_provider": {
                    name: sorted(pages) for name, pages in failed_pages.items()
                },
                "failure_reasons_by_provider": dict(failure_reasons),
                "low_coverage_pages": sorted(low_coverage),
                "needs_review_pages": sorted(needs_review),
                "provider_attempts": attempts,
            }
        )
        return SupplierOfferExtraction(
            supplier_name=supplier_name,
            source_filename=document_path.name,
            items=items,
            warnings=warnings,
            document_metadata=metadata,
        )

    @staticmethod
    def _record_attempt(attempts, failed_pages, failure_reasons, chunk, results, failures, phase):
        for name, reason in failures.items():
            for page in range(chunk.start_page, chunk.end_page + 1):
                failed_pages[name].add(page)
                failure_reasons[name][str(page)] = reason
        attempts.append(
            {
                "phase": phase,
                "start_page": chunk.start_page,
                "end_page": chunk.end_page,
                "failures": failures,
                "results": {
                    name: result.model_dump(mode="json") for name, result in results.items()
                },
            }
        )

    @staticmethod
    def _agreement_metrics(items, unresolved):
        return {
            "agreed_item_count": sum(item.agreement_status.startswith("agreed_") for item in items),
            "single_source_item_count": sum(
                item.agreement_status == "single_source" for item in items
            ),
            "conflict_count": len(unresolved),
            "jev_shadow_count": sum("jev_shadow" in row for row in unresolved),
            "unresolved_offers": unresolved,
        }

    def _resolve_results(self, results, failures, source_filename, supplier_hint, shadow=True):
        items, unresolved = reconcile_items(
            {name: result.items for name, result in results.items()}, self._jev if shadow else None
        )
        warnings = [
            f"{name}: {warning}" for name, result in results.items() for warning in result.warnings
        ]
        if failures:
            warnings.append("No se pudo leer con " + ", ".join(sorted(failures)))
        if unresolved:
            warnings.append("Conflicto entre lectores; las observaciones quedaron para revisión.")
        return SupplierOfferExtraction(
            supplier_name=supplier_hint
            or next((r.supplier_name for r in results.values() if r.supplier_name), None),
            source_filename=source_filename,
            items=items,
            warnings=warnings,
            document_metadata={
                **self._agreement_metrics(items, unresolved),
                "extraction_mode": (
                    "single_source" if len(self._providers) == 1 else "dual_independent"
                ),
                "successful_extraction_providers": sorted(results),
                "failed_extraction_providers": sorted(failures),
            },
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        message = " ".join(str(exc).split())
        if "503" in message or "UNAVAILABLE" in message.upper():
            return "temporarily unavailable"
        if "429" in message:
            return "rate limited"
        return type(exc).__name__


class DualDocumentExtractionProvider(ChunkedDocumentExtractionProvider):
    """Legacy dual reader, retained for offline comparison tests."""

    def __init__(self, providers, **kwargs):
        if len(providers) != 2:
            raise ValueError("Dual extraction requires exactly two distinct providers.")
        super().__init__(providers, **kwargs)
