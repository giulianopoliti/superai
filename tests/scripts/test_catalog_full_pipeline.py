from argparse import Namespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import (
    Base,
    BusinessModel,
    CatalogInterpretationModel,
    ProductFamilyModel,
    ProductModel,
)
from app.providers.catalog_decisions import CatalogDecisionResponse
from app.providers.catalog_taxonomy_proposal import FullCatalogProposal
from scripts import interpret_catalog_full as pipeline


@pytest.mark.parametrize("disputed", [False, True])
@pytest.mark.parametrize("compact", [False,True])
def test_structured_taxonomy_persists_without_closed_option_rejection(
    tmp_path, monkeypatch, disputed, compact
):
    url = "sqlite:///" + (tmp_path / "sample.sqlite3").as_posix()
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(BusinessModel(id="a", name="A"))
        session.flush()
        session.add(
            ProductFamilyModel(
                id="f",
                business_id="a",
                name="LIMPIEZA",
                normalized_name="limpieza",
                external_family_id="1",
            )
        )
        session.flush()
        session.add(
            ProductModel(
                id="p",
                business_id="a",
                name="REPASADOR",
                normalized_name="repasador",
                category="LIMPIEZA",
                family_id="f",
            )
        )
        session.commit()
    p = FullCatalogProposal.model_validate(
        {
            **{
                k: {"value": None, "evidence": None}
                for k in ("category", "subcategory", "brand", "line", "variant", "packaging")
            },
            "category": {"value": "Textiles para el hogar", "evidence": "REPASADOR"},
            "subcategory": {"value": "Repasadores", "evidence": "REPASADOR"},
            "suggested_family": "LIMPIEZA",
            "family_action": "keep",
            "family_reason": "Familia compatible con la organización del negocio",
        }
    )

    class Proposer:
        def __init__(self, *args, **kwargs):
            pass

        def interpret(self, *args):
            return {
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "output": [{"content": [{"parsed": p.model_dump()}]}],
            }, p

    class Chooser:
        prompt = "mock"

        def __init__(self, *args, **kwargs):
            pass

        def decide(self, source, questions):
            assert source["current_family"] == "LIMPIEZA"
            assert bool({"category", "subcategory"} & {q["name"] for q in questions}) == disputed
            answers = []
            for question in questions:
                choice = (
                    question["choices"][0]["value"]
                    if (question["name"] == "original_family")
                    else "not_applicable"
                )
                answers.append(
                    {
                        "name": question["name"],
                        "type": "choice",
                        "choice": choice,
                        "probabilities": [
                            {"value": c["value"], "probability": float(c["value"] == choice)}
                            for c in question["choices"]
                        ],
                    }
                )
            raw = {
                "model": "mock",
                "usage": {"input_tokens": 1, "output_tokens": 0},
                "answers": answers,
            }
            return raw, CatalogDecisionResponse.model_validate(raw)

    monkeypatch.setattr(pipeline, "OpenAITaxonomyProposer", Proposer)
    monkeypatch.setattr(pipeline, "OpenAICatalogDecisions", Chooser)
    monkeypatch.setattr(pipeline.settings, "database_url", url)
    monkeypatch.setattr(pipeline.settings, "openai_api_key", "fake")
    result = pipeline.run_full(
        Namespace(
            business_id="a",
            product_id=["p"],
            limit=1,
            model="gpt-6-luna",
            threshold=0.9,
            output=tmp_path / "report.json",
            review_feedback={"p": "Comprobar ambigüedad"} if disputed else {},
            compact_audit=compact,
        )
    )
    assert not result["errors"] and len(result["results"]) == 1
    with Session(engine) as session:
        row = session.scalar(select(CatalogInterpretationModel))
        if disputed:
            assert row.category is None and row.subcategory is None
            assert row.attributes["category"]["status"] == "needs_review"
            assert row.attributes["category"]["proposed_value"] == "Textiles para el hogar"
        else:
            assert row.category == "Textiles para el hogar" and row.subcategory == "Repasadores"
            assert row.attributes["category"]["probability"] is None
        assert session.get(ProductModel, "p").category == "LIMPIEZA"
