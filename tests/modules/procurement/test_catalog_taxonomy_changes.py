from copy import deepcopy

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.models import Base, BusinessModel, CatalogInterpretationModel, ProductModel
from app.db.repositories.catalog_interpretations import ensure_vocabulary
from app.db.repositories.catalog_taxonomy_changes import (
    apply_taxonomy_changes,
    capture,
    revert_taxonomy_change,
)
from app.modules.procurement.catalog_interpretation import interpretation_columns


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    event.listen(
        engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON")
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([BusinessModel(id="a", name="A"), BusinessModel(id="b", name="B")])
        session.flush()
        yield session


def add_interpretation(session, identifier, concept):
    product = ProductModel(
        id=identifier,
        business_id="a",
        name="POLENTA SYP 500G",
        normalized_name="polenta",
        category="ALMACEN",
    )
    session.add(product)
    session.flush()
    attrs = {
        k: {"status": "unknown", "value": None}
        for k in (
            "product_type",
            "category",
            "subcategory",
            "brand",
            "line",
            "variant",
            "packaging",
            "content",
            "package_quantity",
            "minimum_order_quantity",
        )
    }
    attrs["category"] = {
        "status": "proposed_new",
        "value": None,
        "vocabulary_id": concept.id,
        "evidence": "POLENTA",
    }
    row = CatalogInterpretationModel(
        id=identifier,
        business_id="a",
        product_id=identifier,
        run_id="run",
        source_hash=identifier,
        config_hash="config",
        source={"original_name": product.name},
        attributes=attrs,
        request={},
        response={},
        **interpretation_columns(attrs, product.name),
    )
    session.add(row)
    session.flush()
    return row


@pytest.mark.parametrize(
    "status,reason", [("not_applicable", "not_applicable"), ("needs_review", "insufficient_detail")]
)
def test_vocabulary_refresh_preserves_explicit_empty_reason(database, status, reason):
    root = ensure_vocabulary(database, "a", "category", "Polenta", [], status="proposed")
    row = add_interpretation(database, "p", root)
    state = {"status": status, "value": None, "reason": reason}
    row.attributes = {**row.attributes, "subcategory": state}
    apply_taxonomy_changes(
        database,
        "a",
        [
            {
                "action": "activate",
                "target_id": root.id,
                "reason": "Clasificación revisada",
                "evidence": "POLENTA",
            }
        ],
        {"product_names": ["POLENTA SYP 500G"]},
    )
    assert row.attributes["subcategory"] == state
    if status == "needs_review":
        assert row.status == "needs_review"


def test_compact_activation_audits_only_node_and_keeps_existing_interpretations(database):
    from app.db.repositories.catalog_vocabulary_activation import activate_vocabulary_node

    root = ensure_vocabulary(database, "a", "category", "Polenta", [], status="proposed")
    row = add_interpretation(database, "p", root)
    before = deepcopy(row.attributes)
    change = activate_vocabulary_node(
        database,
        "a",
        root.id,
        {"product_id": "p", "original_name": "POLENTA SYP 500G", "evidence": "POLENTA"},
    )
    assert change.before["vocabulary"][0]["status"] == "proposed"
    assert change.after["vocabulary"][0]["status"] == "active"
    assert change.before["interpretations"] == change.after["interpretations"] == []
    assert row.attributes == before
    assert (
        activate_vocabulary_node(
            database,
            "a",
            root.id,
            {"product_id": "p", "original_name": "POLENTA SYP 500G", "evidence": "POLENTA"},
        )
        is None
    )
    with pytest.raises(ValueError, match="Invalid vocabulary"):
        activate_vocabulary_node(
            database,
            "b",
            root.id,
            {"product_id": "p", "original_name": "POLENTA", "evidence": "POLENTA"},
        )


def test_reorganization_keeps_ids_updates_links_and_reverts(database):
    session = database
    polenta = ensure_vocabulary(session, "a", "category", "Polenta", ["POLENTA"], status="proposed")
    row = add_interpretation(session, "p", polenta)
    before = deepcopy(capture(session, "a"))
    change = apply_taxonomy_changes(
        session,
        "a",
        [
            {
                "action": "create_category",
                "label": "Harinas y sémolas",
                "aliases": [],
                "reason": "Agrupar productos",
                "evidence": "POLENTA",
            },
            {
                "action": "move_to_subcategory",
                "target_id": polenta.id,
                "parent_label": "Harinas y sémolas",
                "reason": "Polenta es un subtipo",
                "evidence": "POLENTA",
            },
        ],
        {"product_names": ["POLENTA SYP 500G"]},
    )
    assert polenta.id == row.attributes["subcategory"]["vocabulary_id"]
    assert row.category == "Harinas y sémolas" and row.subcategory == "Polenta"
    assert session.get(ProductModel, "p").category == "ALMACEN"
    revert_taxonomy_change(session, "a", change.id)
    assert capture(session, "a") == before
    assert change.status == "reverted"


def test_activating_vocabulary_does_not_confirm_uncertain_product(database):
    session = database
    concept = ensure_vocabulary(session, "a", "category", "Caramelos", [], status="proposed")
    row = add_interpretation(session, "uncertain", concept)
    row.attributes = {
        **row.attributes,
        "category": {
            "status": "low_probability",
            "value": None,
            "vocabulary_id": concept.id,
            "probability": 0.26,
            "evidence": "POLENTA",
        },
    }
    session.flush()
    apply_taxonomy_changes(
        session,
        "a",
        [
            {
                "action": "activate",
                "target_id": concept.id,
                "reason": "Aceptar concepto",
                "evidence": "POLENTA",
            }
        ],
        {"product_names": ["POLENTA SYP 500G"]},
    )
    assert row.category is None
    assert row.attributes["category"]["status"] == "low_probability"
    assert session.get(ProductModel, "uncertain").category == "ALMACEN"


def test_uncertain_subtype_does_not_erase_a_confirmed_parent(database):
    session = database
    parent = ensure_vocabulary(session, "a", "category", "Bebidas", [], status="active")
    child = ensure_vocabulary(
        session, "a", "subcategory", "Fernet", [], status="proposed", parent_id=parent.id
    )
    row = add_interpretation(session, "drink", parent)
    row.attributes = {
        **row.attributes,
        "category": {
            "status": "interpreted",
            "value": "Bebidas",
            "vocabulary_id": parent.id,
            "probability": 0.99,
            "evidence": "POLENTA",
        },
        "subcategory": {
            "status": "unsupported",
            "value": None,
            "vocabulary_id": child.id,
            "probability": 0.94,
            "evidence": "POLENTA",
        },
    }
    session.flush()
    apply_taxonomy_changes(
        session,
        "a",
        [
            {
                "action": "activate",
                "target_id": child.id,
                "reason": "Aceptar concepto",
                "evidence": "POLENTA",
            }
        ],
        {"product_names": ["POLENTA SYP 500G"]},
    )
    assert row.category == "Bebidas" and row.subcategory is None


def test_invalid_plan_is_atomic_and_cross_business_targets_are_blocked(database):
    session = database
    concept = ensure_vocabulary(session, "b", "category", "Polenta", [], status="proposed")
    before = capture(session, "a")
    with pytest.raises(ValueError, match="outside"):
        apply_taxonomy_changes(
            session,
            "a",
            [
                {
                    "action": "create_category",
                    "label": "Harinas",
                    "aliases": [],
                    "reason": "Agrupar",
                    "evidence": "POLENTA",
                },
                {
                    "action": "activate",
                    "target_id": concept.id,
                    "reason": "Activar",
                    "evidence": "POLENTA",
                },
            ],
            {"product_names": ["POLENTA"]},
        )
    assert capture(session, "a") == before


def test_rename_preserves_aliases_and_seed_does_not_recreate_old_label(database):
    session = database
    concept = ensure_vocabulary(session, "a", "category", "Harinas", [], status="active")
    change = apply_taxonomy_changes(
        session,
        "a",
        [
            {
                "action": "rename",
                "target_id": concept.id,
                "label": "Harinas y sémolas",
                "reason": "Ampliar etiqueta",
                "evidence": "HARINAS",
            }
        ],
        {"goal": "Organizar HARINAS"},
    )
    reused = ensure_vocabulary(
        session, "a", "category", "Harinas", [], status="active", update_aliases=False
    )
    assert reused.id == concept.id and "Harinas" in concept.aliases
    apply_taxonomy_changes(
        session,
        "a",
        [
            {
                "action": "rename",
                "target_id": concept.id,
                "label": "Harinas y cereales",
                "reason": "Ajustar",
                "evidence": "HARINAS",
            }
        ],
        {"goal": "Organizar HARINAS"},
    )
    with pytest.raises(ValueError, match="subsequent"):
        revert_taxonomy_change(session, "a", change.id)


def test_cycle_is_rejected(database):
    concept = ensure_vocabulary(database, "a", "category", "Polenta", [], status="active")
    with pytest.raises(ValueError, match="cycle"):
        apply_taxonomy_changes(
            database,
            "a",
            [
                {
                    "action": "move_to_subcategory",
                    "target_id": concept.id,
                    "parent_id": concept.id,
                    "reason": "Mover",
                    "evidence": "POLENTA",
                }
            ],
            {"product_names": ["POLENTA"]},
        )


def test_rollback_refreshes_interpretations_created_after_edit(database):
    session = database
    polenta = ensure_vocabulary(session, "a", "category", "Polenta", [], status="proposed")
    change = apply_taxonomy_changes(
        session,
        "a",
        [
            {
                "action": "create_category",
                "label": "Harinas",
                "aliases": [],
                "reason": "Agrupar",
                "evidence": "POLENTA",
            },
            {
                "action": "move_to_subcategory",
                "target_id": polenta.id,
                "parent_label": "Harinas",
                "reason": "Ubicar",
                "evidence": "POLENTA",
            },
        ],
        {"product_names": ["POLENTA"]},
    )
    row = add_interpretation(session, "new", polenta)
    revert_taxonomy_change(session, "a", change.id)
    assert row.attributes["category"]["vocabulary_id"] == polenta.id
    assert row.attributes["category"]["status"] == "proposed_new"
    assert row.attributes["subcategory"].get("vocabulary_id") is None
    assert polenta.attribute == "category" and polenta.parent_id is None
