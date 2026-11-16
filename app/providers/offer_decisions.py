"""Bounded Decisions adapter for the existing OfferDecisionProvider contract.

Uses the documented HTTP endpoint because the installed SDK lacks decisions.
One request, no retries or redirects. Reservations survive in page checkpoints.
"""

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from app.modules.procurement.analysis_contracts import (
    AnalysisCost,
    AnalysisMethod,
    ChoiceDecision,
    OfferDecisionResult,
    ProviderCallRecord,
    TokenUsage,
)
from app.modules.procurement.decision_policy import evidence_text
from app.providers.offer_decision_budget import OfferDecisionBudget

PROMPT_VERSION = "offer_identity_decisions_v2"
MODEL = "gpt-6-luna"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _transport(request, timeout):
    with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        raw = response.read(128_001)
    if len(raw) > 128_000:
        raise ValueError("Response limit")
    return json.loads(raw)


class OpenAIOfferDecisions(OfferDecisionBudget):
    def __init__(
        self,
        *,
        api_key,
        max_calls=6,
        max_spend_usd=Decimal("0.02"),
        max_input_tokens=32000,
        timeout_seconds=20,
        transport=None,
        allowed_offer_ids=None,
    ):
        if not api_key:
            raise ValueError("Missing Decisions key")
        super().__init__(max_calls=max_calls, max_spend_usd=max_spend_usd,
                         max_input_tokens=max_input_tokens, reserve_rate="0.20")
        if not 0 < timeout_seconds <= 60:
            raise ValueError("Invalid Decisions timeout")
        self._key = api_key
        self.timeout_seconds = timeout_seconds
        self._transport = transport or _transport
        self.prompt = (
            Path(__file__).parents[1] / "assistant/prompts" / (PROMPT_VERSION + ".md")
        ).read_text(encoding="utf-8")
        self.config_id = hashlib.sha256(self.prompt.encode()).hexdigest()
        # Ceiling is 2x the documented standard input tariff. Text-only, short context.
        self.allowed_offer_ids = allowed_offer_ids

    def payload(self, request):
        identity = [q for q in request.questions if q.question_id.endswith(":identity")]
        if len(identity) != 1 or len(request.candidates) > 12:
            raise ValueError("Expected one bounded identity question")
        if request.offer.business_id != request.business_id or any(
            c.business_id != request.business_id for c in request.candidates
        ):
            raise ValueError("Tenant mismatch")
        question = identity[0]
        if set(question.options) != {c.candidate_id for c in request.candidates} | {
            "none",
            "insufficient",
        }:
            raise ValueError("Invalid candidate binding")
        # Send only literal identity citations. Prices and purchasing costs are excluded.
        observed = {
            field: evidence_text(request.block, request.offer.field_evidence.get(field, []))
            for field in ("raw_name", "brand", "unit_size", "unit", "retail_contents_count")
        }
        return {
            "model": MODEL,
            "input": self.prompt
            + "\n"
            + json.dumps(
                {
                    "evidence": observed,
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
                ensure_ascii=False,
            ),
            "questions": [
                {
                    "name": question.question_id,
                    "type": "choice",
                    "instructions": question.prompt,
                    "choices": [{"value": value} for value in question.options],
                }
            ],
        }

    def cache_key(self, request):
        # Bind to tenant/evidence/candidates/prompt, independent of continuation IDs.
        payload = self.payload(request)
        payload["questions"][0]["name"] = "identity"
        value = request.business_id + json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(value.encode()).hexdigest()

    def decide_offer(self, *, request, before_send=None):
        if (
            self.allowed_offer_ids is not None
            and request.offer.offer_id not in self.allowed_offer_ids
        ):
            return OfferDecisionResult(
                offer_id=request.offer.offer_id,
                reason_codes=["decisions_outside_evaluation"],
            )
        payload = self.payload(request)
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        # UTF-8 byte count bounds text tokens; reserve protocol overhead separately.
        key = self.cache_key(request)
        bound = self.reserve_payload(key, encoded)
        if bound is None:
            return OfferDecisionResult(
                offer_id=request.offer.offer_id,
                reason_codes=["decisions_budget_or_uncertain"],
            )
        if before_send:
            before_send()  # Durable inflight marker and reservation before network I/O.
        started = monotonic()
        call = ProviderCallRecord(
            call_id=uuid4().hex,
            run_id=request.block.run_id,
            reading_id=request.block.reading_id,
            provider="openai_decisions",
            model=MODEL,
            prompt_version=PROMPT_VERSION + ":" + self.config_id,
            schema_version="offer_identity_choice_v1",
            analysis_method=AnalysisMethod.OPENAI_STRUCTURED,
            phase="decision",
            start_page=request.block.page_number,
            end_page=request.block.page_number,
            status="error",
        )
        result = OfferDecisionResult(offer_id=request.offer.offer_id, calls=[call])
        try:
            raw = self._transport(
                Request(
                    "https://api.openai.com/v1/decisions",
                    data=encoded,
                    method="POST",
                    headers={
                        "Authorization": "Bearer " + self._key,
                        "Content-Type": "application/json",
                    },
                ),
                self.timeout_seconds,
            )
            usage = raw.get("usage", {})
            tokens = usage.get("input_tokens")
            if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
                call.usage = TokenUsage(input_tokens=tokens, output_tokens=0, total_tokens=tokens)
                call.cost = AnalysisCost(
                    amount=Decimal(tokens) * Decimal("0.10") / 1_000_000,
                    currency="USD",
                    tariff_id="decisions_standard_2026_10_22",
                    estimated=True,
                )
                if tokens > bound:
                    self.halted = True
                    raise ValueError("Input reservation exceeded")
            else:
                self.halted = True  # Unknown usage stays unknown; never assumed free.
            question = payload["questions"][0]
            answers = raw.get("answers", [])
            if raw.get("model") != MODEL or len(answers) != 1:
                raise ValueError("Invalid envelope")
            answer = answers[0]
            values = answer.get("probabilities", [])
            probabilities = {v["value"]: v["probability"] for v in values}
            if any(
                isinstance(v, bool) or not isinstance(v, (int, float))
                for v in [answer.get("confidence"), *probabilities.values()]
            ):
                raise ValueError("Expected JSON numbers")
            if (
                answer.get("name") != question["name"]
                or answer.get("type") != "choice"
                or len(values) != len(probabilities)
                or set(probabilities) != {v["value"] for v in question["choices"]}
            ):
                raise ValueError("Invalid options or refusal")
            choice = ChoiceDecision(
                question_id=question["name"],
                choice=answer["choice"],
                confidence=answer["confidence"],
                probabilities=probabilities,
                model=MODEL,
                prompt_version=PROMPT_VERSION + ":" + self.config_id,
            )
            if probabilities[choice.choice] < max(probabilities.values()):
                raise ValueError("Nonmaximum choice")
            result.decisions.append(choice)
            call.status = "success"
        except Exception as exc:
            self.halted = True
            call.safe_error = (
                f"HTTP {exc.code}" if isinstance(exc, HTTPError) else type(exc).__name__
            )
            result.reason_codes.append("decisions_provider_error")
        call.duration_seconds = monotonic() - started
        return result
