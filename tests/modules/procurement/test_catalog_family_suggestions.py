import json
from copy import deepcopy

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Base,
    BusinessModel,
    CatalogInterpretationModel,
    ProductFamilyModel,
    ProductModel,
    UserModel,
)
from app.db.models import (
    CatalogFamilyChangeSuggestionModel as Change,
)
from app.db.models import (
    CatalogFamilySuggestionEventModel as Event,
)
from app.modules.procurement.catalog_family_backfill import materialize_report
from app.modules.procurement.catalog_family_suggestions import FamilySuggestionService
from app.modules.procurement.catalog_interpretation import digest, snapshot
from app.modules.procurement.family_suggestion_schemas import (
    FamilyChangeRequest,
    FamilyCreationRequest,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")

    def connect(connection, _):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "connect", connect)
    event.listen(engine, "begin", lambda connection: connection.exec_driver_sql("BEGIN"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([BusinessModel(id="a", name="A"), BusinessModel(id="b", name="B")])
        session.flush()
        session.add_all(
            [
                UserModel(id="owner", business_id="a", display_name="Responsable", role="owner"),
                UserModel(id="operator", business_id="a", display_name="Operador", role="operator"),
                UserModel(id="other", business_id="b", display_name="Otro", role="owner"),
                ProductFamilyModel(
                    id="animals",
                    business_id="a",
                    name="ALIMENTO PARA ANIMALES",
                    normalized_name="alimento para animales",
                    external_family_id="1",
                ),
                ProductFamilyModel(
                    id="grocery",
                    business_id="a",
                    name="ALMACEN",
                    normalized_name="almacen",
                    external_family_id="2",
                ),
                ProductFamilyModel(
                    id="foreign",
                    business_id="b",
                    name="ALMACEN",
                    normalized_name="almacen",
                    external_family_id="2",
                ),
            ]
        )
        session.flush()
        product = ProductModel(
            id="p",
            business_id="a",
            name="CAFE LA VIRGINIA",
            normalized_name="cafe la virginia",
            category="ALIMENTO PARA ANIMALES",
            family_id="animals",
            external_family_id="1",
        )
        session.add(product)
        session.flush()
        add_interpretation(session, product, "i")
        session.commit()
        yield session


def add_interpretation(db, product, identifier):
    source = snapshot(product)
    row = CatalogInterpretationModel(
        id=identifier,
        business_id=product.business_id,
        product_id=product.id,
        run_id=identifier,
        source_hash=digest(source),
        config_hash=identifier,
        source=source,
        standard_name=product.name,
        status="partial",
        attributes={},
        request={},
        response={
            "original_family_validation": {
                "status": "change_suggested",
                "suggested_family": "ALMACEN",
                "reason": "Es café para personas",
                "evidence": "CAFE",
            }
        },
    )
    db.add(row)
    db.flush()
    return row


def request(**kwargs):
    return FamilyChangeRequest(
        **{
            "product_id": "p",
            "interpretation_id": "i",
            "target_family_id": "grocery",
            "reason": "Es café para personas",
            "evidence": "CAFE",
            **kwargs,
        }
    )


def creation_request(**kwargs):
    return FamilyCreationRequest(
        **{
            "product_id": "p",
            "interpretation_id": "i",
            "proposed_name": "Especiales",
            "description": "Productos con manejo especial",
            "includes": ["Productos especiales"],
            "excludes": ["Productos generales"],
            "reason": "Necesidad operativa",
            "evidence": "CAFE",
            **kwargs,
        }
    )


def test_reexecution_is_idempotent_and_does_not_touch_source(db):
    service = FamilySuggestionService(db, "a")
    before = deepcopy(snapshot(db.get(ProductModel, "p")))
    first, status = service.propose_change(request())
    second, reused = service.propose_change(request())
    db.flush()
    assert status == "inserted" and reused == "reused" and first.id == second.id
    assert db.scalar(select(func.count()).select_from(Change)) == 1
    assert db.scalar(select(func.count()).select_from(Event)) == 1
    assert snapshot(db.get(ProductModel, "p")) == before


def test_review_requires_authorized_actor_and_approval_is_not_application(db):
    service = FamilySuggestionService(db, "a")
    row, _ = service.propose_change(request())
    for actor in ("operator", "other", "missing"):
        with pytest.raises(ValueError, match="Review requires"):
            service.review("change", row.id, "approved", actor, "Revisado")
    service.review("change", row.id, "approved", "owner", "Revisado")
    service.review("change", row.id, "approved", "owner", "Revisado")
    db.flush()
    assert row.status == "approved" and row.applied_at is None
    assert db.get(ProductModel, "p").family_id == "animals"
    assert db.scalar(select(func.count()).select_from(Event)) == 2


def test_rejected_suggestion_does_not_reopen_on_new_prompt(db):
    service = FamilySuggestionService(db, "a")
    row, _ = service.propose_change(request())
    service.review("change", row.id, "rejected", "owner", "Organización del negocio")
    again, outcome = service.propose_change(request(policy_version="another-prompt"))
    assert again.id == row.id and outcome == "rejected"


def test_stale_source_blocks_review_and_audits_invalidation(db):
    service = FamilySuggestionService(db, "a")
    row, _ = service.propose_change(request())
    db.get(ProductModel, "p").name = "CAFE NUEVO 200G"
    service.review("change", row.id, "approved", "owner", "Revisado")
    assert row.status == "stale" and row.reviewed_by is None


def test_cross_business_or_wrong_product_interpretation_is_blocked(db):
    service = FamilySuggestionService(db, "a")
    with pytest.raises(ValueError, match="Family does not belong"):
        service.propose_change(request(target_family_id="foreign"))
    with pytest.raises(ValueError, match="Interpretation"):
        service.propose_change(request(interpretation_id="missing"))
    with pytest.raises(ValueError, match="Product"):
        FamilySuggestionService(db, "b").propose_change(request())


def test_database_independently_blocks_cross_business_target(db):
    row, _ = FamilySuggestionService(db, "a").propose_change(request())
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        row.target_family_id = "foreign"
        db.flush()


def test_creation_grouping_conflicts_and_rejected_dependents(db):
    service = FamilySuggestionService(db, "a")
    family, outcome = service.propose_creation(creation_request())
    reused, _ = service.propose_creation(creation_request())
    assert outcome == "inserted" and reused.id == family.id
    with pytest.raises(ValueError, match="Conflicting definition"):
        service.propose_creation(creation_request(description="Otra definición"))
    movement, _ = service.propose_change(
        request(target_family_id=None, target_creation_suggestion_id=family.id)
    )
    service.review("creation", family.id, "rejected", "owner", "No crear")
    assert family.status == "rejected" and movement.status == "stale"


def test_approving_creation_does_not_approve_movements_or_create_stock_family(db):
    service = FamilySuggestionService(db, "a")
    family, _ = service.propose_creation(creation_request())
    movement, _ = service.propose_change(
        request(target_family_id=None, target_creation_suggestion_id=family.id)
    )
    before = db.scalar(select(func.count()).select_from(ProductFamilyModel))
    service.review("creation", family.id, "approved", "owner", "Crear familia")
    assert movement.status == "pending" and family.created_family_id is None
    assert db.scalar(select(func.count()).select_from(ProductFamilyModel)) == before


def test_reconciliation_requires_real_catalog_change(db):
    service = FamilySuggestionService(db, "a")
    row, _ = service.propose_change(request())
    service.review("change", row.id, "approved", "owner", "Mover café")
    with pytest.raises(ValueError, match="does not confirm"):
        service.reconcile("change", row.id, "owner", "import-1")
    # Simulate the actual external import, not a write by the suggestion service.
    product = db.get(ProductModel, "p")
    product.family_id, product.external_family_id, product.category = "grocery", "2", "ALMACEN"
    assert service.check_stale() == []
    assert row.status == "approved"
    service.reconcile("change", row.id, "owner", "import-2")
    assert row.status == "applied" and row.application_reference == "import-2"


def test_backfill_uses_database_notice_and_rollback_is_effective(db):
    report = {
        "business_id": "a",
        "results": [
            {
                "id": "i",
                "product_id": "p",
                "response": {"original_family_validation": {"suggested_family": "foreign"}},
            }
        ],
    }
    result = materialize_report(db, "a", report)
    assert result["counts"] == {"inserted": 1}
    assert db.scalar(select(Change)).target_family_id == "grocery"
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Change)) == 0


def test_database_requires_exactly_one_target(db):
    row, _ = FamilySuggestionService(db, "a").propose_change(request())
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        row.target_family_id = None
        db.flush()


def test_invalid_evidence_and_existing_creation_are_rejected(db):
    service = FamilySuggestionService(db, "a")
    with pytest.raises(ValueError, match="Evidence"):
        service.propose_change(request(evidence="LECHE"))
    with pytest.raises(ValueError, match="already has"):
        service.propose_creation(creation_request(proposed_name="ALMACEN"))


def test_stale_creation_is_not_inserted_again(db):
    service = FamilySuggestionService(db, "a")
    family, _ = service.propose_creation(creation_request())
    service.review("creation", family.id, "approved", "owner", "Revisar creación")
    assert family.status == "stale"
    repeated, outcome = service.propose_creation(creation_request())
    assert repeated.id == family.id and outcome == "stale"


def test_connector_export_reuses_validation_and_only_inserts_pending_records(db):
    from scripts.prepare_catalog_family_backfill import prepare
    from scripts.review_catalog_families import serialize

    exported = {"business_id": "a", "fingerprint": "verified-export-fingerprint"}
    for model, key in (
        (ProductFamilyModel, "families"),
        (ProductModel, "products"),
        (CatalogInterpretationModel, "interpretations"),
    ):
        exported[key] = [serialize(r) for r in db.scalars(select(model)).all()]
    exported["families"] = [r for r in exported["families"] if r["business_id"] == "a"]
    exported = json.loads(json.dumps(exported, default=str))
    report = {"business_id": "a", "results": [{"id": "i", "product_id": "p"}]}
    sql, result = prepare(exported, report)
    assert result["counts"] == {"inserted": 1}
    assert sql.count("INSERT INTO") == 2
    assert "UPDATE products" not in sql and "UPDATE product_families" not in sql
    assert "verified-export-fingerprint" in sql and "FOR SHARE" in sql
    exported["products"][0]["business_id"] = "b"
    with pytest.raises(ValueError, match="mixes businesses"):
        prepare(exported, report)


def test_operator_cli_dry_run_does_not_persist(db, monkeypatch, tmp_path, capsys):
    import sys

    from scripts import review_catalog_families as cli

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps({"business_id": "a", "results": [{"id": "i", "product_id": "p"}]}),
        encoding="utf-8",
    )
    engine = db.get_bind()
    monkeypatch.setattr(cli, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(
        sys,
        "argv",
        ["review", "backfill", "--business-id", "a", "--report", str(report), "--dry-run"],
    )
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is True and result["counts"] == {"inserted": 1}
    assert db.scalar(select(func.count()).select_from(Change)) == 0
