from decimal import Decimal
from io import BytesIO

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db.models import Base
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.schemas import (
    Product,
    ProductMatchCandidate,
    Supplier,
    SupplierOfferDocument,
    SupplierOfferItem,
    SupplierProductRelationship,
)
from app.settings import settings


def build_procurement_session():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def seed_match_candidate(repository: SqlProcurementRepository):
    product = repository.add_product(
        Product(
            business_id="business-1",
            name="UVITA TINTO TETRABRICK 1L",
            normalized_name="uvita tinto tetrabrick 1l",
            current_cost=Decimal("2088"),
            sale_price=Decimal("2700"),
        )
    )
    supplier = repository.add_supplier(
        Supplier(
            business_id="business-1",
            name="Vital",
            normalized_name="vital",
        )
    )
    document = repository.add_supplier_offer_document(
        SupplierOfferDocument(
            business_id="business-1",
            supplier_id=supplier.id,
            source_filename="vital.pdf",
        )
    )
    item = repository.add_supplier_offer_items(
        [
            SupplierOfferItem(
                business_id="business-1",
                supplier_offer_document_id=document.id,
                supplier_id=supplier.id,
                raw_name="UVITA Vino t/b 1lt",
                normalized_name="uvita vino tetrabrick 1l",
                offer_price=Decimal("1469"),
            )
        ]
    )[0]
    [candidate] = repository.replace_product_match_candidates(
        business_id="business-1",
        supplier_offer_document_id=document.id,
        candidates=[
            ProductMatchCandidate(
                supplier_offer_item=item,
                product=product,
                relationship_type=SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME,
                confidence_score=Decimal("0.8647"),
                reasons=["brand_found"],
                cost_difference=Decimal("619"),
                recommendation="buy",
            )
        ],
    )
    return document, candidate


def test_procurement_api_lists_and_accepts_product_matches(monkeypatch) -> None:
    session = build_procurement_session()
    repository = SqlProcurementRepository(session)
    document, candidate = seed_match_candidate(repository)
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)

    with TestClient(main.create_app()) as client:
        list_response = client.get(
            f"/procurement/supplier-offers/{document.id}/matches",
            params={"business_id": "business-1"},
        )
        accept_response = client.post(
            f"/procurement/product-matches/{candidate.id}/accept",
            json={
                "business_id": "business-1",
                "reviewed_by_user_id": "user-1",
                "notes": "Coincide",
            },
        )
        reviewed_response = client.get(
            f"/procurement/supplier-offers/{document.id}/matches",
            params={"business_id": "business-1"},
        )

    assert list_response.status_code == 200
    assert list_response.json()["candidates"][0]["candidate"]["id"] == candidate.id
    assert accept_response.status_code == 200
    assert accept_response.json()["accepted"] is True
    assert (
        reviewed_response.json()["candidates"][0]["candidate"]["status"]
        == "accepted"
    )


def test_procurement_api_imports_document_with_local_text_provider(monkeypatch, tmp_path) -> None:
    session = build_procurement_session()
    repository = SqlProcurementRepository(session)
    repository.add_product(
        Product(
            business_id="business-1",
            name="UVITA TINTO TETRABRICK 1L",
            normalized_name="uvita tinto tetrabrick 1l",
            current_cost=Decimal("2088"),
            sale_price=Decimal("2700"),
        )
    )
    offer_path = tmp_path / "vital.txt"
    offer_path.write_text("UVITA Vino t/b 1lt $1469\n", encoding="utf-8")
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)

    with TestClient(main.create_app()) as client:
        with offer_path.open("rb") as file:
            response = client.post(
                "/procurement/supplier-offers/from-document",
                data={
                    "business_id": "business-1",
                    "supplier_name": "Vital",
                    "extraction_provider": "local_text",
                    "persist_candidates": "true",
                },
                files={"file": ("vital.txt", file, "text/plain")},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["import_result"]["document"]["source_filename"] == "vital.txt"
    assert body["import_result"]["items"][0]["raw_name"] == "UVITA Vino t/b 1lt"
    assert body["comparison"]["persisted_count"] == 1
    assert body["comparison"]["report"]["matched_count"] == 1


def test_procurement_api_imports_catalog_from_file(monkeypatch) -> None:
    session = build_procurement_session()
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)
    csv_content = (
        b"id;scodproducto;sean;snombre;sfamilia;rpreciou;rcostou;rmargenganancia;rstock;bactivo\n"
        b"1;A1;779;UVITA TINTO TETRABRICK 1L;VINOS;2700;2088;20;4;T\n"
    )

    with TestClient(main.create_app()) as client:
        response = client.post(
            "/procurement/catalog-imports/from-file",
            data={"business_id": "business-1"},
            files={"file": ("productos.csv", BytesIO(csv_content), "text/csv")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["source_filename"] == "productos.csv"
    assert body["row_count"] == 1
    assert body["imported_count"] == 1


def test_procurement_api_lists_supplier_offer_documents(monkeypatch) -> None:
    session = build_procurement_session()
    repository = SqlProcurementRepository(session)
    document, _ = seed_match_candidate(repository)
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)

    with TestClient(main.create_app()) as client:
        response = client.get(
            "/procurement/supplier-offers",
            params={"business_id": "business-1"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body[0]["id"] == document.id
    assert body[0]["source_filename"] == "vital.pdf"


def test_procurement_ui_is_served(client) -> None:
    response = client.get("/procurement-ui", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/ui/index.html"
