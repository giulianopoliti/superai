"""Bounded generative proposal only for auxiliary taxonomy gaps."""

import json
from pathlib import Path

from openai import OpenAI

from app.providers.catalog_taxonomy_proposal import TaxonomyProposal

PROMPT = (Path(__file__).parents[1] / "assistant/prompts/catalog_auxiliary_gap.md").read_text(
    encoding="utf-8"
)


class OpenAIAuxiliaryGap:
    def __init__(self, api_key, *, effort="none", on_response=None):
        if effort not in {"none", "low"}:
            raise ValueError("Only none/low are supported for auxiliary gaps")
        self.effort = effort
        self.on_response = on_response
        self.client = OpenAI(api_key=api_key, timeout=45, max_retries=0)

    def propose(self, source, attribute, entries, parent_id):
        if attribute not in {"category", "subcategory"}:
            raise ValueError("Only auxiliary category/subcategory creation is allowed")
        response = self.client.responses.create(
            model="gpt-6-luna",
            instructions=PROMPT,
            input=json.dumps(
                {
                    "original_name": source["original_name"],
                    "attribute": attribute,
                    "parent_id": parent_id,
                    "vocabulary": entries,
                },
                ensure_ascii=False,
            ),
            reasoning={"effort": self.effort},
            max_output_tokens=800,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "auxiliary_taxonomy_gap",
                    "schema": TaxonomyProposal.model_json_schema(),
                    "strict": True,
                }
            },
        )
        raw = response.model_dump(mode="json")
        if self.on_response:
            self.on_response(raw)
        return raw, TaxonomyProposal.model_validate_json(response.output_text)
