"""Optional Jev classifier for read-only shadow evaluations."""

import json
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from app.modules.procurement.jev_pilot import JevChoice
from app.modules.procurement.schemas import ProductMatchCandidate, SupplierOfferItem


class _ChoiceAnswer(BaseModel):
    type: str
    choice: str
    confidence: Decimal = Field(ge=0, le=1)
    probabilities: dict[str, Decimal]


class _Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class _Response(BaseModel):
    model: str
    answers: dict[str, _ChoiceAnswer]
    usage: _Usage


class JevHttpChooser:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 20,
        prompt_path: Path | None = None,
    ) -> None:
        if not api_key or not model or model.endswith("-latest"):
            raise ValueError("Jev shadow requires an API key and a pinned model version")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._prompt = (
            prompt_path or Path(__file__).parents[1] / "assistant/prompts/jev_product_choice.md"
        ).read_text(encoding="utf-8")

    def choose(
        self, *, item: SupplierOfferItem, candidates: list[ProductMatchCandidate]
    ) -> JevChoice:
        criteria = {
            candidate.product.id: (
                f"{candidate.product.name}; marca={candidate.product.brand or 'desconocida'}; "
                f"presentación={candidate.product.unit_size or '?'} "
                f"{candidate.product.unit or '?'}"
            )
            for candidate in candidates
            if candidate.product is not None
        }
        criteria.update(
            {
                "none": "Ningún producto corresponde a la misma presentación.",
                "insufficient": "Falta evidencia de marca, tamaño o variante para decidir.",
            }
        )
        payload = {
            "state": {
                "offer": {
                    "name": item.raw_name,
                    "brand": item.brand,
                    "unit_size": str(item.unit_size) if item.unit_size is not None else None,
                    "unit": item.unit,
                    "package_quantity": item.package_quantity,
                }
            },
            "model": self._model,
            "questions": {
                "product": {
                    "type": "choice",
                    "instructions": self._prompt,
                    "criteria": criteria,
                }
            },
        }
        request = Request(
            "https://api.typesafe.ai/v1/systemone",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=self._timeout) as response:
            result = _Response.model_validate_json(response.read())
        answer = result.answers["product"]
        if (
            result.model != self._model
            or answer.type != "choice"
            or answer.choice not in criteria
            or set(answer.probabilities) != set(criteria)
        ):
            raise ValueError("Invalid Jev choice response")
        return JevChoice(
            product_id=answer.choice if answer.choice not in {"none", "insufficient"} else None,
            confidence=answer.confidence,
            model_version=result.model,
            probabilities=answer.probabilities,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        )


class JevHttpOfferReconciler:
    """Closed Choice questions translated to a validated shadow decision."""

    def __init__(self, *, api_key: str, model: str, timeout_seconds: float = 20) -> None:
        if not api_key or not model or model.endswith("-latest"):
            raise ValueError("Jev shadow requires an API key and a pinned model version")
        self._api_key, self._model, self._timeout = api_key, model, timeout_seconds
        self._prompt = (
            Path(__file__).parents[1] / "assistant/prompts/jev_offer_reconciliation.md"
        ).read_text(encoding="utf-8")

    def reconcile(self, *, observations: dict[str, object]):
        from app.providers.documents.reconciliation import OfferReconciliationDecision

        if set(observations) != {"gemini", "openai"}:
            raise ValueError("Expected one Gemini/OpenAI pair")
        reason_options = {
            "same": "Mismo producto, marca, presentación y precio.",
            "different": "Hay diferencias de producto, presentación o precio.",
            "unclear": "Falta evidencia para confirmar la coincidencia.",
        }
        criteria = {
            "decision": {
                "same_offer": "Ambas lecturas son la misma oferta comercial.",
                "different_offer": "Las lecturas describen ofertas diferentes.",
                "insufficient": "La evidencia no permite decidir.",
            },
            "selected_source": {
                "gemini": "Gemini ofrece la observación más completa.",
                "openai": "OpenAI ofrece la observación más completa.",
                "none": "No hay una base segura para seleccionar.",
            },
            "reason": reason_options,
        }
        fields = (
            "raw_name",
            "brand",
            "unit_size",
            "unit",
            "package_quantity",
            "offer_price",
            "currency",
            "price_type",
            "tax_included",
        )
        for field in fields:
            criteria[field] = {
                "compatible": f"{field}: datos compatibles, incluso para completar un faltante.",
                "conflicting": f"{field}: datos contradictorios.",
                "unknown": f"{field}: evidencia insuficiente.",
            }
        request = Request(
            "https://api.typesafe.ai/v1/systemone",
            data=json.dumps(
                {
                    "state": {"observations": observations},
                    "model": self._model,
                    "questions": {
                        key: {"type": "choice", "instructions": self._prompt, "criteria": options}
                        for key, options in criteria.items()
                    },
                }
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=self._timeout) as response:
            result = _Response.model_validate_json(response.read())
        if result.model != self._model or set(result.answers) != set(criteria):
            raise ValueError("Unexpected Jev response model/questions")
        for key, options in criteria.items():
            answer = result.answers[key]
            if (
                answer.type != "choice"
                or answer.choice not in options
                or set(answer.probabilities) != set(options)
                or any(p < 0 or p > 1 for p in answer.probabilities.values())
                or abs(sum(answer.probabilities.values()) - 1) > Decimal("0.01")
            ):
                raise ValueError("Invalid Jev reconciliation answer")
        answer = result.answers["decision"]
        source = result.answers["selected_source"].choice
        fillable = {"brand", "unit_size", "unit", "package_quantity", "tax_included"}
        return OfferReconciliationDecision(
            decision=answer.choice,
            confidence=answer.confidence,
            selected_source=source if source != "none" else None,
            compatible_fields=[
                field
                for field in fields
                if field in fillable and result.answers[field].choice == "compatible"
            ],
            conflicting_fields=[
                field for field in fields if result.answers[field].choice == "conflicting"
            ],
            reason=reason_options[result.answers["reason"].choice],
        )
