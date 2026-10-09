"""Closed textual JEV decisions. No repositories, retries, vision or commercial writes."""

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import monotonic
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from pydantic import BaseModel, Field, StrictInt, ValidationError, field_validator

from app.modules.procurement.analysis_contracts import (
    CONTRACT_VERSION,
    AnalysisMethod,
    BlockClassificationRequest,
    BlockClassificationResult,
    ChoiceDecision,
    ChoiceQuestion,
    FragmentClassification,
    OfferDecisionRequest,
    OfferDecisionResult,
    OfferTextBlock,
    ProviderCallRecord,
    TokenUsage,
    ValueCandidate,
)
from app.modules.procurement.block_interpretation import (
    classification_option_sets,
    detect_value_candidates,
    price_occurrences,
    validate_value_candidates,
    value_question_id,
)

PROBABILITY_SUM_TOLERANCE = Decimal("0.001")  # Shared supplier_offer_analysis_v1 contract.
ENDPOINT = "https://api.typesafe.ai/v1/systemone"


class _Answer(BaseModel):
    type: str
    choice: str
    confidence: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    probabilities: dict[str, Decimal]

    @field_validator("confidence", mode="before")
    @classmethod
    def numeric_confidence(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValueError("Expected JSON number")
        return value

    @field_validator("probabilities", mode="before")
    @classmethod
    def numeric_probabilities(cls, value):
        if not isinstance(value, dict) or any(
            isinstance(p, bool) or not isinstance(p, (int, float, Decimal)) for p in value.values()
        ):
            raise ValueError("Expected JSON probability numbers")
        return value


class _Usage(BaseModel):
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)


class _Envelope(BaseModel):
    model: str
    answers: dict[str, object]


@dataclass(frozen=True)
class _Evaluation:
    """Private transport result; shared domain DTOs are built by the adapter below."""

    answers: dict[str, _Answer] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_seconds: float = 0
    attempted: bool = False
    error: str | None = None
    error_detail: str | None = None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Prevent forwarding Authorization or silently making another HTTP call.
        return None


def _http_transport(request: Request, timeout: float, max_bytes: int) -> bytes:
    with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("response_too_large")
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


class JevAnalysisClient:
    """One POST per evaluation; caller owns execution-wide deadline/call budget.

    Socket timeout bounds transport operations. Monotonic deadline also rejects
    late results and includes semaphore waiting; it is not OS-level cancellation.
    Pass the SAME client to workers to share concurrency limits.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "jev-1.13.0",
        timeout_seconds: float = 20,
        max_concurrency: int = 2,
        max_request_bytes: int = 100_000,
        max_response_bytes: int = 1_000_000,
        max_questions: int = 128,
        max_calls: int = 64,
        transport: Callable[[Request, float, int], bytes] | None = None,
        clock: Callable[[], float] = monotonic,
    ):
        if not api_key.strip() or not re.fullmatch(r"jev-\d+\.\d+\.\d+", model):
            raise ValueError("JEV requires a key and a fixed version ID")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if any(
            isinstance(n, bool) or not isinstance(n, int) or n < 1
            for n in (
                max_concurrency,
                max_request_bytes,
                max_response_bytes,
                max_questions,
                max_calls,
            )
        ):
            raise ValueError("JEV limits must be positive integers")
        self.model = model
        self._key = api_key
        self._timeout = timeout_seconds
        self._request_limit = max_request_bytes
        self._response_limit = max_response_bytes
        self._question_limit = max_questions
        self._call_limit = max_calls
        self._transport = transport or _http_transport
        self._clock = clock
        self._slots = BoundedSemaphore(max_concurrency)
        self._lock = Lock()
        self.call_count = 0
        self._batch_start_call_count = 0
        self.error_count = 0

    def start_batch(self):
        """Renew the batch quota after callers have drained active transports."""
        with self._lock:
            self._batch_start_call_count = self.call_count

    def request_bytes(self, *, state: dict, questions: dict) -> int:
        return len(self._encode(state, questions))

    def _encode(self, state, questions):
        return json.dumps(
            {"model": self.model, "state": state, "questions": questions},
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")

    def preflight(self, *, state: dict, questions: dict) -> _Evaluation | None:
        """Local wire validation; no semaphore, budget reservation or transport."""
        for question in questions.values():
            options = question.get("criteria")
            if (
                question.get("type") != "choice"
                or not question.get("instructions")
                or not isinstance(options, dict)
                or not options
                or any(not isinstance(key, str) or not key for key in options)
            ):
                raise ValueError("Invalid closed Choice request")
        detail = (
            "empty_questions"
            if not questions
            else "question_limit"
            if len(questions) > self._question_limit
            else "options_limit"
            if any(len(q["criteria"]) > 255 for q in questions.values())
            else "request_bytes_limit"
            if self.request_bytes(state=state, questions=questions) > self._request_limit
            else None
        )
        return _Evaluation(error="request_limit", error_detail=detail) if detail else None

    def evaluate(
        self,
        *,
        state: dict,
        questions: dict,
        deadline: float | None = None,
        reserve_call: Callable[[], bool] | None = None,
    ) -> _Evaluation:
        start = self._clock()
        if deadline is not None and not math.isfinite(deadline):
            raise ValueError("deadline must be finite")
        end = (
            min(start + self._timeout, deadline) if deadline is not None else start + self._timeout
        )
        local_error = self.preflight(state=state, questions=questions)
        if local_error:
            return local_error
        data = self._encode(state, questions)
        remaining = end - self._clock()
        if remaining <= 0 or not self._slots.acquire(timeout=max(remaining, 0)):
            return _Evaluation(error="deadline_exceeded", duration_seconds=self._clock() - start)
        attempted = False
        usage = None
        try:
            remaining = end - self._clock()
            if remaining <= 0:
                return _Evaluation(
                    error="deadline_exceeded", duration_seconds=self._clock() - start
                )
            request = Request(
                ENDPOINT,
                data=data,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
            )
            with self._lock:
                if self.call_count - self._batch_start_call_count >= self._call_limit:
                    return _Evaluation(error="call_budget_exceeded")
                if reserve_call is not None and not reserve_call():
                    return _Evaluation(error="call_budget_exceeded")
                self.call_count += 1
            attempted = True
            raw = self._transport(request, remaining, self._response_limit)
            if len(raw) > self._response_limit:
                raise ValueError("response_too_large")
            decoded = json.loads(raw, object_pairs_hook=_unique_object, parse_float=Decimal)
            if isinstance(decoded, dict):
                try:
                    usage = _Usage.model_validate(decoded.get("usage"))
                except ValidationError:
                    pass  # Unknown usage is never zero; valid answers remain usable proposals.
            envelope = _Envelope.model_validate(decoded)
            if envelope.model != self.model or set(envelope.answers) - set(questions):
                raise ValueError("invalid_envelope")
            if self._clock() > end:
                raise TimeoutError()
            answers, errors = {}, {}
            for key, question in questions.items():
                try:
                    answer = _Answer.model_validate(envelope.answers.get(key))
                    values = answer.probabilities
                    invalid = (
                        "invalid_answer_type"
                        if answer.type != "choice"
                        else "invalid_answer_choice"
                        if answer.choice not in question["criteria"]
                        else "invalid_answer_options"
                        if set(values) != set(question["criteria"])
                        else "invalid_answer_probability_range"
                        if any(not p.is_finite() or not 0 <= p <= 1 for p in values.values())
                        else "invalid_answer_probability_sum"
                        if abs(sum(values.values()) - 1) > PROBABILITY_SUM_TOLERANCE
                        else "invalid_answer_not_maximum"
                        if values[answer.choice] < max(values.values())
                        else None
                    )
                    if invalid:
                        errors[key] = invalid
                        continue
                    answers[key] = answer
                except (ValidationError, ValueError):
                    errors[key] = (
                        "missing_answer" if key not in envelope.answers else "invalid_answer_schema"
                    )
            if errors:
                with self._lock:
                    self.error_count += 1
            return _Evaluation(
                answers=answers,
                errors=errors,
                attempted=True,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                duration_seconds=self._clock() - start,
            )
        except Exception as exc:
            with self._lock:
                self.error_count += 1
            # Never expose provider body, key, user text or exception strings.
            error = f"http_{exc.code}" if isinstance(exc, HTTPError) else type(exc).__name__
            return _Evaluation(
                attempted=attempted,
                error=error,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                duration_seconds=self._clock() - start,
            )
        finally:
            self._slots.release()


FRAGMENT_CATEGORIES = (
    "product_name",
    "brand",
    "variant",
    "retail_size",
    "retail_contents",
    "commercial_quantity",
    "current_price",
    "previous_price",
    "reference_price",
    "minimum_purchase",
    "promotion",
    "tax_condition",
    "other",
    "uncertain",
    "mixed",
)
FRAGMENT_PROMPT_VERSION = "jev_fragment_classification_v2"
DECISION_PROMPT_VERSION = "jev_offer_decision_v2"


def _block_questions(
    block: OfferTextBlock, candidates: list[ValueCandidate]
) -> list[ChoiceQuestion]:
    specification = classification_option_sets(block, candidates)
    result = [
        ChoiceQuestion(
            question_id=f"fragment:{index}",
            options=list(FRAGMENT_CATEGORIES),
            prompt=f"Classify `block.fragments[{index}].text` in the context of `block.full_text`.",
        )
        for index, _ in enumerate(block.fragments)
    ]
    text_ids = [c.candidate_id for c in candidates if c.kind == "text"]
    size_ids = [c.candidate_id for c in candidates if c.kind == "size"]
    count_ids = [
        c.candidate_id
        for c in candidates
        if c.kind in {"number", "quantity"}
        and c.value is not None
        and c.value > 0
        and c.value == c.value.to_integral_value()
    ]
    fragments = block.fragments + [
        f for f in block.page_conditions if f.fragment_id in block.condition_refs
    ]
    for index, price in enumerate(price_occurrences(block, candidates)):
        target = (
            f"Evaluate occurrence {price.candidate_id!r} in `occurrences` "
            "against `block.full_text` "
            "and explicitly linked `block.page_conditions`. This question has no other answers. "
        )
        fields = {
            "role": (
                ["current_price", "previous_price", "reference_price", "not_price", "uncertain"],
                "Classify THIS occurrence: current/previous absolute price, "
                "reference per litre/kg/piece, not_price, uncertain.",
            ),
            "raw_name": (
                text_ids + ["none", "insufficient"],
                "Select the existing text span naming the product/variant for THIS price.",
            ),
            "unit_size": (
                size_ids + ["none", "insufficient"],
                "Select retail net size for the product priced by THIS occurrence.",
            ),
            "brand": (
                text_ids + ["none", "insufficient"],
                "Select the literal brand line associated with THIS product; "
                "do not guess from catalogue or memory.",
            ),
            "package_quantity": (
                count_ids + ["none", "insufficient"],
                "Select sellable items in the commercial bundle for THIS price; "
                "never retail contents or minimum purchase.",
            ),
            "retail_contents_count": (
                count_ids + ["none", "insufficient"],
                "Select contents INSIDE one retail item priced here.",
            ),
            "minimum_purchase": (
                count_ids + ["none", "insufficient"],
                "Select minimum purchase count for THIS price.",
            ),
            "minimum_scope": (
                ["offer", "page", "unknown"],
                "Classify minimum purchase scope for THIS price: offer/page/unknown.",
            ),
            "price_type": (
                ["unit", "pack", "unknown"],
                "Classify THIS amount: unit price, pack total, unknown.",
            ),
            "basis_evidence": (
                [f.fragment_id for f in fragments] + ["none", "insufficient"],
                "Select the EXISTING fragment that explicitly states price basis for THIS amount. "
                "A linked page condition is allowed; minimum/contents are not price basis.",
            ),
        }
        for field_name, (options, prompt) in fields.items():
            question_id = value_question_id(index, field_name)
            if question_id not in specification:
                continue
            # Backend defines admissible typed bindings and omits irrelevant fields.
            options = sorted(specification[question_id])
            result.append(
                ChoiceQuestion(
                    question_id=question_id,
                    options=options,
                    prompt=target + prompt,
                )
            )
    if {q.question_id: set(q.options) for q in result} != classification_option_sets(
        block, candidates
    ):
        raise ValueError("Inconsistent classification question specification")
    return result


def build_classification_questions(
    *,
    block: OfferTextBlock,
    candidates: list[ValueCandidate],
) -> list[ChoiceQuestion]:
    """Use the backend's actual candidate IDs; never rebuild them behind its back."""
    validate_value_candidates(block=block, candidates=candidates)
    return _block_questions(block, candidates)


def prepare_block_request(
    *,
    business_id: str,
    block: OfferTextBlock,
    candidates: list[ValueCandidate] | None = None,
) -> BlockClassificationRequest:
    """Backend entry point: local occurrence detection, then one batch of closed questions."""
    candidates = detect_value_candidates(block=block) if candidates is None else candidates
    return BlockClassificationRequest(
        business_id=business_id,
        block=block,
        candidates=candidates,
        questions=build_classification_questions(block=block, candidates=candidates),
    )


def _validate_offer_refs(request: OfferDecisionRequest):
    if (
        not request.business_id
        or request.offer.business_id != request.business_id
        or request.offer.block_id != request.block.block_id
    ):
        raise ValueError("Offer belongs to another business/block")
    allowed = {f.fragment_id: f for f in request.block.fragments}
    allowed.update(
        {
            f.fragment_id: f
            for f in request.block.page_conditions
            if f.fragment_id in request.block.condition_refs
        }
    )
    for refs in request.offer.field_evidence.values():
        for ref in refs:
            fragment = allowed.get(ref.fragment_id)
            if fragment is None or not ref.quote or (ref.start is None) != (ref.end is None):
                raise ValueError("Invalid offer evidence reference")
            if ref.start is None:
                if ref.quote not in fragment.text:
                    raise ValueError("Altered evidence quote")
            elif (
                ref.end <= ref.start
                or ref.end > len(fragment.text)
                or fragment.text[ref.start : ref.end] != ref.quote
            ):
                raise ValueError("Altered evidence offsets")
    ids, products = set(), set()
    for candidate in request.candidates:
        if (
            candidate.business_id != request.business_id
            or not candidate.candidate_id
            or not candidate.product_id.strip()
            or candidate.candidate_id in ids
            or candidate.product_id in products
            or candidate.candidate_id in {"none", "insufficient"}
        ):
            raise ValueError("Foreign/duplicate/reserved identity candidate")
        ids.add(candidate.candidate_id)
        products.add(candidate.product_id)


def _block_state(block: OfferTextBlock):
    state = block.model_dump(
        mode="json", include={"block_id", "page_number", "full_text", "fragments", "condition_refs"}
    )
    state["page_conditions"] = [
        f.model_dump(mode="json")
        for f in block.page_conditions
        if f.fragment_id in block.condition_refs
    ]
    return state


class JevAnalysisProvider:
    """Shared-contract classifier/decision adapter. Create once per analysis execution."""

    supports_call_reservation = True

    def start_batch(self):
        self.client.start_batch()

    def __init__(
        self,
        *,
        client: JevAnalysisClient,
        analysis_method: AnalysisMethod = AnalysisMethod.OPENAI_BLOCKS_JEV,
        deadline: float | None = None,
    ):
        self.client = client
        self.analysis_method = analysis_method
        self.deadline = deadline
        self.acceptance_thresholds = None
        self._binding = None
        self._binding_lock = Lock()
        directory = Path(__file__).parents[1] / "assistant" / "prompts"
        self._fragment_prompt = (directory / "jev_fragment_classification.md").read_text(
            encoding="utf-8"
        )
        self._decision_prompt = (directory / "jev_offer_decision.md").read_text(encoding="utf-8")

    def _bind(self, business_id, run_id):
        if not business_id.strip() or not run_id.strip():
            raise ValueError("business_id and run_id are required")
        with self._binding_lock:
            if self._binding is None:
                self._binding = (business_id, run_id)
            elif self._binding != (business_id, run_id):
                raise ValueError("JEV provider belongs to another business/execution")

    def _wire(self, *, state, questions, prompt, descriptions=None):
        if len({q.question_id for q in questions}) != len(questions):
            raise ValueError("Duplicate question IDs")
        state = {**state, "policy_rules": prompt}
        if self.acceptance_thresholds is not None:
            state["acceptance_thresholds"] = dict(self.acceptance_thresholds)
        if descriptions:
            state["candidate_descriptions"] = descriptions
        payload = {
            q.question_id: {
                "type": "choice",
                "instructions": f"Apply state.policy_rules. {q.prompt}",
                "criteria": {option: None for option in q.options},
            }
            for q in questions
        }
        return state, payload

    def _partitions(self, *, state, payload):
        """Greedy stable partitions of independent questions; evidence never truncated."""
        parts, local_errors, current = [], {}, {}
        for key, question in payload.items():
            trial = {**current, key: question}
            if self.client.preflight(state=state, questions=trial) is None:
                current = trial
                continue
            if current:
                parts.append(current)
                current = {}
            local = self.client.preflight(state=state, questions={key: question})
            if local:
                local_errors[key] = (
                    f"jev_{local.error}:{local.error_detail or 'invalid_request'}:{key}"
                )
            else:
                current = {key: question}
        if current:
            parts.append(current)
        return parts, local_errors

    def _evaluate(
        self,
        *,
        block,
        state,
        questions,
        phase,
        prompt,
        version,
        descriptions=None,
        reserve_call=None,
    ):
        state, payload = self._wire(
            state=state, questions=questions, prompt=prompt, descriptions=descriptions
        )
        partitions, local_errors = self._partitions(state=state, payload=payload)
        calls, decisions, reasons = [], [], list(local_errors.values())
        for part in partitions:
            result = self.client.evaluate(
                state=state, questions=part, deadline=self.deadline, reserve_call=reserve_call
            )
            if result.attempted:
                status = "error" if result.error else "incomplete" if result.errors else "success"
                usage = TokenUsage(
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    total_tokens=(result.input_tokens + result.output_tokens)
                    if result.input_tokens is not None and result.output_tokens is not None
                    else None,
                )
                calls.append(
                    ProviderCallRecord(
                        call_id=str(uuid4()),
                        run_id=block.run_id,
                        reading_id=block.reading_id,
                        provider="jev",
                        model=self.client.model,
                        prompt_version=version,
                        schema_version=CONTRACT_VERSION,
                        analysis_method=self.analysis_method,
                        phase=phase,
                        start_page=block.page_number,
                        end_page=block.page_number,
                        duration_seconds=result.duration_seconds,
                        status=status,
                        safe_error=result.error or ("partial_answers" if result.errors else None),
                        usage=usage,
                    )
                )
            decisions.extend(
                ChoiceDecision(
                    question_id=key,
                    choice=answer.choice,
                    confidence=float(answer.confidence),
                    probabilities={option: float(p) for option, p in answer.probabilities.items()},
                    model=self.client.model,
                    prompt_version=version,
                )
                for key, answer in result.answers.items()
            )
            reasons.extend(
                ([f"jev_{result.error}"] if result.error else [])
                + [f"jev_{error}:{key}" for key, error in result.errors.items()]
            )
            if result.error_detail:
                reasons.append(f"jev_{result.error_detail}")
            if result.error in {"call_budget_exceeded", "deadline_exceeded"}:
                break
        return decisions, calls, reasons

    def _classification_inputs(self, request):
        self._bind(request.business_id, request.block.run_id)
        if not request.business_id:
            raise ValueError("business_id is required")
        validate_value_candidates(block=request.block, candidates=request.candidates)
        fragments = request.block.fragments + [
            f
            for f in request.block.page_conditions
            if f.fragment_id in request.block.condition_refs
        ]
        if {f.fragment_id for f in fragments} & {"none", "insufficient"} or {
            f.fragment_id for f in fragments
        } & {c.candidate_id for c in request.candidates}:
            raise ValueError("Reserved or colliding fragment IDs")
        expected = {q.question_id: q for q in _block_questions(request.block, request.candidates)}
        # Caller may deliberately request a subset; assembly keeps missing fields pending.
        for question in request.questions:
            allowed = expected.get(question.question_id)
            if allowed is None or set(question.options) != set(allowed.options):
                raise ValueError("Unknown classification question/options")
        questions = [expected[q.question_id] for q in request.questions]
        return dict(
            block=request.block,
            state={
                "block": _block_state(request.block),
                "occurrences": [c.model_dump(mode="json") for c in request.candidates],
            },
            questions=questions,
            phase="classification",
            prompt=self._fragment_prompt,
            version=FRAGMENT_PROMPT_VERSION,
        )

    def classify_block(
        self, *, request: BlockClassificationRequest, reserve_call=None
    ) -> BlockClassificationResult:
        inputs = self._classification_inputs(request)
        decisions, calls, reasons = self._evaluate(
            **inputs,
            reserve_call=reserve_call,
        )
        classifications = []
        answers = {d.question_id: d for d in decisions}
        for index, fragment in enumerate(request.block.fragments):
            answer = answers.get(f"fragment:{index}")
            if answer:
                classifications.append(
                    FragmentClassification(
                        fragment_id=fragment.fragment_id,
                        category=answer.choice,
                        decision=answer,
                    )
                )
        return BlockClassificationResult(
            run_id=request.block.run_id,
            block_id=request.block.block_id,
            decisions=decisions,
            classifications=classifications,
            calls=calls,
            reason_codes=reasons,
        )

    def _decision_inputs(self, request):
        self._bind(request.business_id, request.block.run_id)
        _validate_offer_refs(request)
        questions = []
        descriptions = {
            c.candidate_id: {
                "name": c.name,
                "brand": c.brand,
                "unit_size": str(c.unit_size) if c.unit_size is not None else None,
                "unit": c.unit,
            }
            for c in request.candidates
        }
        for question in request.questions:
            purpose = question.question_id.rsplit(":", 1)[-1]
            if question.question_id not in {purpose, f"{request.offer.offer_id}:{purpose}"}:
                raise ValueError("Question belongs to another offer")
            options = (
                (set(descriptions) | {"none", "insufficient"})
                if purpose == "identity"
                else {
                    "unit",
                    "pack",
                    "unknown",
                }
                if purpose == "price_basis"
                else set()
            )
            if not options or set(question.options) != options:
                raise ValueError("Unknown offer decision question/options")
            target = (
                "Select identity using `offer.raw_name`, `offer.brand`, `offer.unit_size`, "
                "`offer.unit`, `offer.field_evidence` and candidate descriptions; ignore price."
                if purpose == "identity"
                else "Select the price basis for `offer.offer_price` using its references in "
                "`offer.field_evidence` and `block.full_text`; ignore catalogue packaging."
            )
            questions.append(
                ChoiceQuestion(
                    question_id=question.question_id,
                    options=question.options,
                    prompt=target,
                )
            )
        # No unit costs, DB configuration, entire catalogue or write actions enter the state.
        return dict(
            block=request.block,
            state={
                "block": _block_state(request.block),
                "offer": request.offer.model_dump(mode="json"),
            },
            questions=questions,
            phase="decision",
            prompt=self._decision_prompt,
            version=DECISION_PROMPT_VERSION,
            descriptions=descriptions,
        )

    def _request_plan(self, request):
        inputs = (
            self._classification_inputs(request)
            if isinstance(request, BlockClassificationRequest)
            else self._decision_inputs(request)
        )
        state, payload = self._wire(
            state=inputs["state"],
            questions=inputs["questions"],
            prompt=inputs["prompt"],
            descriptions=inputs.get("descriptions"),
        )
        return self._partitions(state=state, payload=payload)

    def planned_call_count(self, *, request):
        return len(self._request_plan(request)[0])

    def preflight(self, *, request):
        parts, errors = self._request_plan(request)
        if parts:
            return None
        reasons = list(errors.values()) or ["jev_no_questions"]
        if isinstance(request, BlockClassificationRequest):
            return BlockClassificationResult(
                run_id=request.block.run_id, block_id=request.block.block_id, reason_codes=reasons
            )
        return OfferDecisionResult(offer_id=request.offer.offer_id, reason_codes=reasons)

    def decide_offer(
        self, *, request: OfferDecisionRequest, reserve_call=None
    ) -> OfferDecisionResult:
        decisions, calls, reasons = self._evaluate(
            **self._decision_inputs(request), reserve_call=reserve_call
        )
        return OfferDecisionResult(
            offer_id=request.offer.offer_id,
            decisions=decisions,
            calls=calls,
            reason_codes=reasons,
        )
