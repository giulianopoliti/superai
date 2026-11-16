from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.providers.catalog_taxonomy_proposal import FullCatalogProposal, OpenAITaxonomyProposer


@pytest.mark.parametrize("failures", [1, 3])
def test_invalid_structured_output_has_bounded_retries_and_usage_warning(failures):
    proposal = FullCatalogProposal.model_validate(
        {
            **{
                k: {"value": None, "evidence": None}
                for k in ("category", "subcategory", "brand", "line", "variant", "packaging")
            },
            "suggested_family": None,
            "family_reason": None,
        }
    )
    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        if len(calls) <= failures:
            FullCatalogProposal.model_validate({})
        return SimpleNamespace(
            output_parsed=proposal, model_dump=lambda **_: {"usage": {"input_tokens": 1}}
        )

    provider = OpenAITaxonomyProposer("fake")
    provider.client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    if failures == 3:
        with pytest.raises(ValidationError):
            provider.interpret({"original_name": "ASD"}, [], [])
        assert len(calls) == 3
    else:
        raw, parsed = provider.interpret({"original_name": "ASD"}, [], [])
        assert parsed == proposal and raw["transport_attempts"] == 2
        assert len(raw["validation_attempt_errors"]) == 1
        assert len(calls) == 2
