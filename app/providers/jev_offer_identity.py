"""JEV identity and independent group membership through existing domain contracts."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.modules.procurement.analysis_contracts import (
    AnalysisCost,
    AnalysisMethod,
    CandidateMembershipDecision,
    ChoiceDecision,
    ChoiceQuestion,
    OfferDecisionResult,
    ProviderCallRecord,
    TokenUsage,
)
from app.modules.procurement.decision_policy import DocumentDecisionPolicy, evidence_text
from app.modules.procurement.identity import physical_size
from app.providers.jev_analysis import JevAnalysisClient
from app.providers.offer_decision_budget import OfferDecisionBudget

VERSION = "jev_offer_identity_groups_v1"


class JevOfferIdentityProvider(OfferDecisionBudget):
    def __init__(
        self,
        *,
        api_key,
        model="jev-1.13.0",
        max_calls=6,
        max_spend_usd=Decimal("0.02"),
        max_input_tokens=32000,
        timeout_seconds=20,
        transport=None,
        allowed_offer_ids=None,
    ):
        super().__init__(
            max_calls=max_calls,
            max_spend_usd=max_spend_usd,
            max_input_tokens=max_input_tokens,
            reserve_rate="0.10",
        )
        self.model = model
        self.client = JevAnalysisClient(
            api_key=api_key,
            model=model,
            max_calls=20,
            max_questions=25,
            max_request_bytes=28000,
            max_response_bytes=128000,
            max_concurrency=1,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        directory = Path(__file__).parents[1] / "assistant/prompts"
        self.prompt = (directory / "offer_group_membership_v1.md").read_text(encoding="utf-8")
        self.identity_prompt = (directory / "offer_identity_decisions_v2.md").read_text(
            encoding="utf-8",
        )
        self.config_id = hashlib.sha256(
            (VERSION + model + self.prompt + self.identity_prompt).encode()
        ).hexdigest()
        self.allowed_offer_ids = allowed_offer_ids

    def prepare_request(self, request):
        clean = DocumentDecisionPolicy().validate_decision(request=request, decision=None).offer
        clean.reason_codes = list(request.offer.reason_codes)
        identity = [q for q in request.questions if q.question_id.endswith(":identity")]
        if not identity or not clean.raw_name or not clean.unit_size:
            return request.model_copy(update={"questions": []})
        questions = list(identity)
        for candidate in request.candidates:
            for purpose, options in (
                ("compatibility", ["compatible", "incompatible", "insufficient"]),
                ("inclusion", ["included", "excluded", "insufficient"]),
            ):
                questions.append(
                    ChoiceQuestion(
                        question_id=f"{clean.offer_id}:{candidate.candidate_id}:{purpose}",
                        options=options,
                        prompt=f"Apply state.instructions. Evaluate {purpose} independently for "
                        f"candidate {candidate.candidate_id}: {candidate.name}.",
                    )
                )
        return request.model_copy(update={"offer": clean, "questions": questions})

    def payload(self, request):
        if (
            request.offer.business_id != request.business_id
            or any(c.business_id != request.business_id for c in request.candidates)
            or not 1 <= len(request.candidates) <= 12
        ):
            raise ValueError("Invalid identity candidate binding")
        size = physical_size(request.offer.unit_size, request.offer.unit)
        citations = {
            field: evidence_text(request.block, request.offer.field_evidence.get(field, []))
            for field in ("raw_name", "brand", "unit_size", "unit", "retail_contents_count")
        }
        candidates = {c.candidate_id: c for c in request.candidates}
        expected_questions = {f"{request.offer.offer_id}:identity"} | {
            f"{request.offer.offer_id}:{candidate_id}:{purpose}"
            for candidate_id in candidates for purpose in ("compatibility", "inclusion")
        }
        if (len(request.questions) != len(expected_questions)
                or {q.question_id for q in request.questions} != expected_questions):
            raise ValueError("Identity and membership questions must be prepared before transport")
        questions = {}
        for q in request.questions:
            identity = q.question_id.endswith(":identity")
            if identity and set(q.options) != set(candidates) | {"none", "insufficient"}:
                raise ValueError("Invalid closed identity options")
            if not identity:
                expected_options = (
                    {"compatible", "incompatible", "insufficient"}
                    if q.question_id.endswith(":compatibility")
                    else {"included", "excluded", "insufficient"}
                )
                if set(q.options) != expected_options:
                    raise ValueError("Invalid closed membership options")
            questions[q.question_id] = {
                "type": "choice",
                "instructions": self.identity_prompt if identity else q.prompt,
                "criteria": {
                    option: candidates[option].name if option in candidates else option
                    for option in q.options
                },
            }
        return {
            "model": self.model,
            "state": {
                "instructions": self.prompt,
                "evidence": citations,
                "observed": {
                    "name": request.offer.raw_name,
                    "brand": request.offer.brand,
                    "size": str(size[0]) if size else None,
                    "unit": size[1] if size else None,
                },
                "candidates": [
                    {
                        "id": c.candidate_id,
                        "name": c.name,
                        "brand": c.brand,
                        "unit_size": str(c.unit_size) if c.unit_size is not None else None,
                        "unit": c.unit,
                    }
                    for c in request.candidates
                ],
            },
            "questions": questions,
        }

    def cache_key(self, request):
        payload = self.payload(request)
        payload["questions"] = {
            key.removeprefix(request.offer.offer_id + ":"): value
            for key, value in payload["questions"].items()
        }
        value = request.business_id + self.config_id + json.dumps(payload, sort_keys=True)
        return hashlib.sha256(value.encode()).hexdigest()

    def decide_offer(self, *, request, before_send=None):
        if (
            self.allowed_offer_ids is not None
            and request.offer.offer_id not in self.allowed_offer_ids
        ):
            return OfferDecisionResult(
                offer_id=request.offer.offer_id,
                reason_codes=["identity_outside_evaluation"],
            )
        payload = self.payload(request)
        kwargs = {k: payload[k] for k in ("state", "questions")}
        if self.client.preflight(**kwargs):
            return OfferDecisionResult(
                offer_id=request.offer.offer_id,
                reason_codes=["identity_request_limit"],
            )
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        bound = self.reserve_payload(self.cache_key(request), encoded)
        if bound is None:
            return OfferDecisionResult(
                offer_id=request.offer.offer_id,
                reason_codes=["identity_budget_or_uncertain"],
            )
        if before_send:
            before_send()
        response = self.client.evaluate(**kwargs)
        call = ProviderCallRecord(
            call_id=uuid4().hex,
            run_id=request.block.run_id,
            reading_id=request.block.reading_id,
            provider="jev_identity",
            model=self.model,
            prompt_version=VERSION + ":" + self.config_id,
            schema_version="identity_and_membership_v1",
            analysis_method=AnalysisMethod.OPENAI_STRUCTURED,
            phase="decision",
            start_page=request.block.page_number,
            end_page=request.block.page_number,
            duration_seconds=response.duration_seconds,
            status="error",
            safe_error=response.error,
        )
        if response.input_tokens is not None:
            call.usage = TokenUsage(
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                total_tokens=response.input_tokens + response.output_tokens
                if response.output_tokens is not None
                else None,
            )
            call.cost = AnalysisCost(
                amount=Decimal(response.input_tokens) * Decimal("0.042") / 1_000_000,
                currency="USD",
                tariff_id="jev_standard_2026_10_22",
                estimated=True,
            )
        result = OfferDecisionResult(offer_id=request.offer.offer_id, calls=[call])
        if (
            response.error
            or response.errors
            or set(response.answers) != set(payload["questions"])
            or response.input_tokens is not None
            and response.input_tokens > bound
        ):
            self.halted = True
            result.reason_codes.append("jev_identity_provider_error")
            return result
        if response.input_tokens is None:
            self.halted = True
        converted = {
            key: ChoiceDecision(
                question_id=key,
                choice=answer.choice,
                confidence=float(answer.confidence),
                probabilities={k: float(v) for k, v in answer.probabilities.items()},
                model=self.model,
                prompt_version=VERSION + ":" + self.config_id,
            )
            for key, answer in response.answers.items()
        }
        result.decisions = [v for k, v in converted.items() if k.endswith(":identity")]
        result.memberships = [
            CandidateMembershipDecision(
                candidate_id=c.candidate_id,
                compatibility=converted[f"{request.offer.offer_id}:{c.candidate_id}:compatibility"],
                inclusion=converted[f"{request.offer.offer_id}:{c.candidate_id}:inclusion"],
            )
            for c in request.candidates
        ]
        call.status = "success"
        return result
