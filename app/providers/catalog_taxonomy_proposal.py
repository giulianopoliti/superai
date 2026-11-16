"""Structured generative fallback for taxonomy gaps, with no persistence."""

import json
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class TaxonomyProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["reuse", "create", "unknown"]
    existing_id: str | None
    label: str | None = Field(max_length=120)
    parent_id: str | None
    aliases: list[str] = Field(max_length=8)
    evidence: str | None = Field(max_length=500)


class TaxonomyEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create_category", "activate", "rename", "move_to_subcategory"]
    target_id: str | None
    label: str | None = Field(max_length=120)
    parent_id: str | None
    parent_label: str | None = Field(max_length=120)
    aliases: list[str] = Field(max_length=8)
    reason: str = Field(min_length=1, max_length=500)
    evidence: str = Field(min_length=1, max_length=500)


class TaxonomyEditPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operations: list[TaxonomyEdit] = Field(max_length=10)


class ProposedField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str | None = Field(max_length=120)
    evidence: str | None = Field(max_length=500)


class NewFamilyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    includes: list[str] = Field(min_length=1, max_length=15)
    excludes: list[str] = Field(max_length=15)


class FullCatalogProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: ProposedField
    subcategory: ProposedField
    brand: ProposedField
    line: ProposedField
    variant: ProposedField
    packaging: ProposedField
    suggested_family: str | None = Field(max_length=120)
    family_reason: str | None = Field(max_length=500)
    category_empty_reason: Literal["unidentifiable", "not_applicable"] | None = None
    subcategory_empty_reason: (
        Literal["unidentifiable", "not_applicable", "insufficient_detail"] | None
    ) = None
    family_action: Literal["keep", "reassign", "propose_new", "unknown"] | None = None
    new_family: NewFamilyDefinition | None = None


class OpenAITaxonomyProposer:
    def __init__(self, api_key: str, *, model: str = "gpt-6-luna"):
        self.model = model
        self.client = OpenAI(api_key=api_key, timeout=45, max_retries=0)
        self.prompt = (
            Path(__file__).parents[1] / "assistant/prompts/catalog_taxonomy_proposal.md"
        ).read_text(encoding="utf-8")

    def interpret(self, source: dict, entries: list[dict], original_families: list[str]):
        prompt = (
            Path(__file__).parents[1] / "assistant/prompts/catalog_full_interpretation.md"
        ).read_text(encoding="utf-8")
        failures = []
        for attempt in range(3):
            try:
                response = self.client.responses.parse(
                    model=self.model,
                    instructions=prompt,
                    input=json.dumps(
                        {
                            "article": source,
                            "vocabulary": entries,
                            "original_families": original_families,
                        },
                        ensure_ascii=False,
                    ),
                    reasoning={"effort": "none"},
                    max_output_tokens=1800,
                    text_format=FullCatalogProposal,
                )
                break
            except ValidationError as exc:
                failures.append(exc.errors(include_input=False, include_context=False))
                if attempt == 2:
                    raise
        if not isinstance(response.output_parsed, FullCatalogProposal):
            raise ValueError("Invalid complete catalog interpretation")
        raw = response.model_dump(mode="json", warnings=False)
        raw["transport_attempts"] = len(failures) + 1
        if failures:
            raw["validation_attempt_errors"] = failures
        return raw, response.output_parsed

    def reorganize(self, goal: str, entries: list[dict], sources: list[dict]):
        prompt = (
            Path(__file__).parents[1] / "assistant/prompts/catalog_taxonomy_maintenance.md"
        ).read_text(encoding="utf-8")
        response = self.client.responses.parse(
            model=self.model,
            instructions=prompt,
            input=json.dumps(
                {
                    "goal": goal,
                    "vocabulary": entries,
                    "articles": [{k: v for k, v in s.items() if k != "category"} for s in sources],
                },
                ensure_ascii=False,
            ),
            reasoning={"effort": "none"},
            max_output_tokens=2500,
            text_format=TaxonomyEditPlan,
        )
        if not isinstance(response.output_parsed, TaxonomyEditPlan):
            raise ValueError("Invalid taxonomy edit plan")
        return response.model_dump(mode="json", warnings=False), response.output_parsed

    def propose(self, source: dict, attribute: str, entries: list[dict], parent_id: str | None):
        response = self.client.responses.parse(
            model=self.model,
            instructions=self.prompt,
            input=json.dumps(
                {
                    "article": source,
                    "attribute": attribute,
                    "parent_id": parent_id,
                    "vocabulary": entries,
                },
                ensure_ascii=False,
            ),
            reasoning={"effort": "none"},
            max_output_tokens=1200,
            text_format=TaxonomyProposal,
        )
        if not isinstance(response.output_parsed, TaxonomyProposal):
            raise ValueError("OpenAI no devolvió una propuesta estructurada válida")
        # SDK 2.x generics emit spurious serialization warnings for parsed outputs.
        # The actual proposal has already passed the explicit Pydantic schema.
        return response.model_dump(mode="json", warnings=False), response.output_parsed
