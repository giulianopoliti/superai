from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.db.models import (
    Base,
    BusinessModel,
    CatalogInterpretationModel,
    CatalogVocabularyModel,
    ProductModel,
)
from app.db.repositories.catalog_interpretations import ensure_vocabulary, save_interpretation
from app.modules.procurement.catalog_interpretation import (
    build_options,
    build_questions,
    digest,
    snapshot,
    validate_answers,
)
from app.providers.catalog_decisions import CatalogDecisionResponse


def response_for(options, selections):
    return CatalogDecisionResponse.model_validate(
        {
            "model": "gpt-6-luna",
            "usage": {"input_tokens": 100},
            "answers": [
                {
                    "type": "choice",
                    "name": q["name"],
                    "choice": selections.get(q["name"], "unknown"),
                    "confidence": 1,
                    "probabilities": [
                        {
                            "value": c["value"],
                            "probability": int(c["value"] == selections.get(q["name"], "unknown")),
                        }
                        for c in q["choices"]
                    ],
                }
                for q in build_questions(options)
            ],
        }
    )


def test_content_pack_and_minimum_are_separate_and_bare_count_is_unknown():
    source = {"original_name": "PACK X6 CAFE 170GR COMPRA MINIMA 12"}
    options = build_options(source, [])
    assert options["content"][0].data == {"value": "170", "unit": "g"}
    assert options["package_quantity"][0].data == 6
    assert options["minimum_order_quantity"][0].data == 12
    bare = build_options({"original_name": "CAFE EN SOBRESITO 30U"}, [])
    assert bare["package_quantity"] == []
    assert bare["content"] == []


def test_original_does_not_become_origen_and_absent_variant_stays_unknown():
    vocab = [
        SimpleNamespace(
            id="origen",
            attribute="line",
            label="Origen",
            aliases=["ORIGEN"],
        )
    ]
    options = build_options({"original_name": "CAFE DOLCA ORIGINAL 170G"}, vocab)
    assert "origen" not in {c.value for c in options["line"]}
    result = validate_answers(response_for(options, {}), options, threshold=0.9)
    assert result["variant"]["value"] is None


def test_equivalent_source_size_and_known_aliases_are_not_duplicate_choices():
    vocabulary = [
        SimpleNamespace(
            id="coffee",
            attribute="product_type",
            label="Café instantáneo",
            aliases=["CAFE INSTANTANEO"],
        ),
        SimpleNamespace(id="origin", attribute="line", label="Origen", aliases=["ORIGEN"]),
    ]
    options = build_options(
        {"original_name": "CAFE INSTANTANEO ORIGEN 170GR", "unit_size": "170.000", "unit": "g"},
        vocabulary,
    )
    assert len(options["content"]) == 1
    assert not any(
        c.origin == "new_fragment" and c.label == "CAFE INSTANTANEO"
        for c in options["product_type"]
    )
    assert not any(c.label == "ORIGEN" for c in options["variant"])


def test_new_fragment_is_proposal_and_unknown_choices_are_rejected():
    options = build_options({"original_name": "AGUA BENEDICTINO 1,5L"}, [])
    fragment = next(c for c in options["brand"] if c.label == "BENEDICTINO")
    response = response_for(options, {"brand": fragment.value, "content": "size_0"})
    result = validate_answers(response, options, threshold=0.9)
    assert result["brand"]["status"] == "proposed_new"
    assert result["brand"]["value"] is None
    assert result["brand"]["evidence"] == "BENEDICTINO"
    assert Decimal(result["content"]["value"]["value"]) == Decimal("1.5")
    response.answers[0].choice = "hallucinated_id"
    with pytest.raises(ValueError):
        validate_answers(response, options, threshold=0.9)


def test_box_weight_is_not_accepted_as_inner_unit_weight():
    source = {"original_name": "CAJA BON O BON X30U 450GR"}
    options = build_options(source, [])
    result = validate_answers(
        response_for(options, {"content": "size_0"}), options, threshold=0.9, source=source
    )
    assert result["content"]["status"] == "ambiguous_container"
    assert result["content"]["value"] is None


def test_tenant_scope_deduplication_and_stale_source():
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([BusinessModel(id="a", name="A"), BusinessModel(id="b", name="B")])
        session.flush()
        product = ProductModel(id="p", business_id="a", name="CAFE 170G", normalized_name="cafe")
        session.add(product)
        session.flush()
        first = ensure_vocabulary(session, "a", "brand", "Nescafé", [], status="proposed")
        second = ensure_vocabulary(session, "a", "brand", "NESCAFE", [], status="proposed")
        other = ensure_vocabulary(session, "b", "brand", "Nescafé", [], status="proposed")
        assert first.id == second.id != other.id
        assert len(session.scalars(select(CatalogVocabularyModel)).all()) == 2
        source = snapshot(product)
        row = CatalogInterpretationModel(
            id="i",
            run_id="run",
            business_id="b",
            product_id="p",
            source_hash=digest(source),
            config_hash="config",
            source=source,
            standard_name="Café",
            status="partial",
            attributes={},
            request={},
            response={},
        )
        with pytest.raises(ValueError, match="belong"):
            save_interpretation(session, row)
        row.business_id = "a"
        product.name = "CAFE 200G"
        session.flush()
        with pytest.raises(ValueError, match="changed"):
            save_interpretation(session, row)
        product.name = "CAFE 170G"
        session.flush()
        saved = save_interpretation(session, row)
        session.commit()
        assert saved.product_id == "p"
        assert product.name == "CAFE 170G"
