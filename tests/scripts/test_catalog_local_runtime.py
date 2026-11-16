from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as backend
from app.db.models import Base, CatalogInterpretationModel, ProductMatchCandidateModel
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.catalog_interpretation import digest, snapshot
from app.modules.procurement.schemas import (
    Product,
    ProductMatchCandidate,
    Supplier,
    SupplierOfferDocument,
    SupplierOfferItem,
)
from app.settings import settings
from scripts.catalog_local_runtime import compose


@pytest.fixture
def local_app(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repo = SqlProcurementRepository(factory)
    p = repo.add_product(Product(
        business_id="a", name="Cacao Nesquik 360 g", normalized_name="cacao nesquik 360 g",
        external_product_id="pos", brand="Nesquik", current_cost=Decimal(200),
    ))
    supplier = repo.add_supplier(Supplier(
        business_id="a", name="Proveedor real de fixture", normalized_name="proveedor fixture",
    ))
    document = repo.add_supplier_offer_document(SupplierOfferDocument(
        business_id="a", supplier_id=supplier.id, source_filename="lista-guardada.pdf",
    ))
    [item] = repo.add_supplier_offer_items([SupplierOfferItem(
        business_id="a", supplier_id=supplier.id, supplier_offer_document_id=document.id,
        raw_name=p.name, normalized_name=p.normalized_name, offer_price=100, brand="Nesquik",
    )])
    for name in ("app_env", "web_auth_enabled", "jobs_enabled", "scheduler_enabled", "llm_enabled",
                 "web_cors_origins", "openai_api_key", "gemini_api_key", "typesafe_api_key",
                 "kapso_api_key"):
        monkeypatch.setattr(settings, name, getattr(settings, name))
    monkeypatch.setattr(backend, "SessionLocal", backend.SessionLocal)
    monkeypatch.setattr(backend, "ProductMatchReviewService", backend.ProductMatchReviewService)
    monkeypatch.setattr(settings, "default_business_id", "a")
    app = compose(factory, business_id="a")
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        yield client, repo, factory, p, document, item


def test_ui_reads_recomputed_matches_and_purchase_plan_without_persisting(local_app):
    client, _, factory, p, document, _ = local_app
    response = client.get(f"/procurement/supplier-offers/{document.id}/matches?business_id=a")
    assert response.status_code == 200
    [entry] = response.json()["candidates"]
    assert entry["product"]["id"] == p.id
    assert entry["candidate"]["status"] == "accepted"
    params = {"business_id": "a", "date_from": date(2026, 1, 1), "date_to": date(2026, 1, 31)}
    plan = client.get(f"/procurement/supplier-offers/{document.id}/purchase-plan", params=params)
    assert plan.status_code == 200
    assert plan.json()["items"][0]["candidate"]["id"] == entry["candidate"]["id"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ProductMatchCandidateModel)) == 0
    assert settings.openai_api_key is None and settings.gemini_api_key is None


def test_ui_keeps_human_choice_but_reports_conflict_without_updating_it(local_app):
    client, repo, factory, p, document, item = local_app
    conflicting = repo.add_product(Product(
        business_id="a", name="Arlistan cafe 360 g", normalized_name="arlistan cafe 360 g",
        brand="Arlistan", external_product_id="other",
    ))
    [saved] = repo.replace_product_match_candidates(
        business_id="a", supplier_offer_document_id=document.id,
        candidates=[ProductMatchCandidate(
            supplier_offer_item=item, product=conflicting, relationship_type="exact_match",
            confidence_score=1, reasons=["corrected_by_user"],
        )],
    )
    with factory() as session:
        session.get(ProductMatchCandidateModel, saved.id).status = "accepted"
        session.commit()
    response = client.get(f"/procurement/supplier-offers/{document.id}/matches?business_id=a")
    [entry] = response.json()["candidates"]
    assert entry["product"]["id"] == conflicting.id
    assert "brand_conflict" in entry["candidate"]["reasons"]
    assert entry["candidate"]["recommendation"] == "review"
    with factory() as session:
        record = session.get(ProductMatchCandidateModel, saved.id)
        assert record.reasons == ["corrected_by_user"] and record.status == "accepted"


def test_local_comparator_ignores_saved_classification_and_preserves_it(local_app):
    client, _, factory, p, document, _ = local_app
    with factory() as session:
        session.add(CatalogInterpretationModel(
            id="classification", run_id="fixture", business_id="a", product_id=p.id,
            source_hash=digest(snapshot(p)), config_hash="fixture", source=snapshot(p),
            standard_name="Cafe Arlistan", status="interpreted",
            attributes={"brand": {
                "status": "interpreted", "value": "Arlistan", "evidence": p.name,
            }}, request={"prompt": "# catalog_decisions_first v1\nfixture"}, response={},
        ))
        session.commit()

    response = client.get(f"/procurement/supplier-offers/{document.id}/matches?business_id=a")
    assert response.status_code == 200
    [entry] = response.json()["candidates"]
    assert entry["product"]["name"] == p.name
    assert entry["product"]["auxiliary"] is None
    assert entry["candidate"]["status"] == "accepted"
    assert "brand_conflict" not in entry["candidate"]["reasons"]
    found = client.get("/procurement/products/search?business_id=a&q=Arlistan")
    assert found.status_code == 200 and found.json() == []
    with factory() as session:
        assert session.get(CatalogInterpretationModel, "classification") is not None
        assert session.scalar(select(func.count()).select_from(ProductMatchCandidateModel)) == 0


def test_local_boundary_blocks_writes_other_tenants_and_foreign_hosts(local_app):
    client, _, _, _, _, _ = local_app
    assert client.post("/procurement/supplier-offers/from-document").status_code == 405
    assert client.post("/assistant/message", json={}).status_code == 405
    assert client.get("/procurement/products?business_id=b").status_code == 403
    assert client.get("/health", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/health", headers={"Origin": "https://attacker.example"}).status_code == 403
    html = client.get("/ui/").text
    assert "Prueba local con tus datos reales" in html and "/local/catalog-preview.js" in html
    assert '"authEnabled": false' in client.get("/ui/config.js").text
    assert client.get("/local/catalog-status").json() == {
        "total": 1, "interpreted": 0, "original_only": 1,
    }
