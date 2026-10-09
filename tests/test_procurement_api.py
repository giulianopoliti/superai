import logging
from decimal import Decimal
from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db.models import Base
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.db.repositories.sql_sales import SqlSalesRepository
from app.modules.procurement.schemas import (
    Product,
    ProductMatchCandidate,
    Supplier,
    SupplierOfferDocument,
    SupplierOfferItem,
    SupplierProductRelationship,
)
from app.modules.sales.sales_import import PosSalesImportService
from app.settings import settings


def build_procurement_session():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_document_pack_review_accepts_identity_without_suggesting_purchase(client, monkeypatch):
    from pypdf import PdfWriter

    from app.modules.procurement.pack_review import PackReviewDecision
    from app.modules.procurement.schemas import ExtractedSupplierOfferItem, SupplierOfferExtraction

    factory = build_procurement_session()
    repository = SqlProcurementRepository(factory)
    repository.add_product(Product(
        business_id="business-1", name="KIMBIES TOALLITAS HUMEDAS X48U",
        normalized_name="kimbies toallitas humedas x48u", brand="KIMBIES",
        current_cost=Decimal(1000), unit_size=48, unit="unit",
    ))
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(settings, "openai_api_key", "fake-test-key")
    calls = []

    class Reader:
        def extract_supplier_offer(self, **kwargs):
            return SupplierOfferExtraction(supplier_name="Vital", items=[ExtractedSupplierOfferItem(
                raw_name="KIMBIES Toallas humedas x48u", brand="KIMBIES", unit_size=48,
                unit="u", offer_price=1500, price_type="pack", package_quantity=48, page_number=1,
            )])

    class Reviewer:
        def __init__(self, **kwargs):
            pass

        def review(self, **kwargs):
            calls.append(kwargs)
            assert len(PdfReader(BytesIO(kwargs["attachment"])).pages) == 1
            return PackReviewDecision(
                price_type="unit", package_quantity=None, retail_contents_count=48,
                confidence=0.99, evidence="Sobre de 48 toallitas", reason="Precio por sobre",
            )

    monkeypatch.setattr(main, "build_supplier_offer_document_provider", lambda _: Reader())
    monkeypatch.setattr(main, "OpenAIPackReviewer", Reviewer)
    pdf, writer = BytesIO(), PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    response = client.post(
        "/procurement/supplier-offers/from-document",
        data={"business_id": "business-1", "extraction_provider": "auto"},
        files={"file": ("offers.pdf", pdf.getvalue(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(calls) == 1
    [item] = data["import_result"]["items"]
    assert item["price_type"] == "unit"
    assert item["package_quantity"] is None
    assert Decimal(item["comparable_unit_price"]) == 1500
    [match] = data["comparison"]["report"]["candidates"]
    assert match["auto_accepted"]
    assert match["recommendation"] == "do_not_buy"
    document_id = data["import_result"]["document"]["id"]
    [saved] = repository.list_product_match_candidates(
        business_id="business-1", supplier_offer_document_id=document_id,
    )
    assert saved.status.value == "accepted"
    assert "auto_accepted_identity_v1" in saved.reasons


def seed_match_candidate(repository: SqlProcurementRepository):
    product = repository.add_product(
        Product(
            business_id="business-1",
            external_product_id="pos-product-1",
            sku="UV1",
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
    assert reviewed_response.json()["candidates"][0]["candidate"]["status"] == "accepted"


def test_procurement_api_imports_document_with_local_text_provider(
    monkeypatch, tmp_path, caplog
) -> None:
    session = build_procurement_session()
    repository = SqlProcurementRepository(session)
    repository.add_product(
        Product(
            business_id="business-1",
            external_product_id="pos-product-1",
            sku="UV1",
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

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
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
    assert "catalog_analysis" in caplog.text
    assert "stage=document_extracted" in caplog.text
    assert "stage=candidates_persisted" in caplog.text
    assert "catalog_match" in caplog.text
    assert "stage=products_loaded" in caplog.text


def test_procurement_api_accepts_non_ascii_supplier_offer_filename(
    monkeypatch,
    tmp_path,
) -> None:
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
    offer_path = tmp_path / "LISTA DE PRECIO Nº1 29MAYO2026.txt"
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
                files={"file": (offer_path.name, file, "text/plain")},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["import_result"]["document"]["source_filename"] == offer_path.name


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
    assert body["summary"]["created_count"] == 1

    with TestClient(main.create_app()) as client:
        status_response = client.get(
            "/procurement/catalog-imports/status", params={"business_id": "business-1"}
        )

    assert status_response.status_code == 200
    assert status_response.json()["total_products"] == 1
    assert status_response.json()["last_import"]["id"] == body["id"]


def test_procurement_api_imports_catalog_with_families_file(monkeypatch) -> None:
    session = build_procurement_session()
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)

    with TestClient(main.create_app()) as client:
        response = client.post(
            "/procurement/catalog-imports/from-file",
            data={"business_id": "business-1"},
            files={
                "file": (
                    "productos.csv",
                    b"id;snombre;sfamilia;idfamilia\nproduct-1;PROMO PATA;ALMACEN;family-1\n",
                    "text/csv",
                ),
                "families_file": (
                    "familias.csv",
                    b"id;sfamilia;bactivo\nfamily-1;CARNICERIA;T\n",
                    "text/csv",
                ),
            },
        )

    assert response.status_code == 200
    assert response.json()["summary"]["categories"] == [
        {"name": "CARNICERIA", "count": 1}
    ]


def test_procurement_api_searches_products(monkeypatch) -> None:
    session = build_procurement_session()
    repository = SqlProcurementRepository(session)
    product = repository.add_product(
        Product(
            business_id="business-1",
            external_product_id="pos-product-1",
            sku="UV1",
            barcode="779123",
            name="UVITA TINTO TETRABRICK 1L",
            normalized_name="uvita tinto tetrabrick 1l",
            current_cost=Decimal("2088"),
            sale_price=Decimal("2700"),
        )
    )
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)

    with TestClient(main.create_app()) as client:
        response = client.get(
            "/procurement/products/search",
            params={"business_id": "business-1", "q": "uvita", "limit": 10},
        )

    assert response.status_code == 200
    body = response.json()
    assert body[0]["id"] == product.id
    assert body[0]["sku"] == "UV1"


def test_procurement_api_records_and_compares_multiple_supplier_prices(
    monkeypatch,
) -> None:
    session = build_procurement_session()
    repository = SqlProcurementRepository(session)
    product = repository.add_product(
        Product(
            business_id="business-1",
            name="COCA COLA 2.25L",
            normalized_name="coca cola 2.25l",
            current_cost=Decimal("1850"),
            sale_price=Decimal("2400"),
        )
    )
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)

    with TestClient(main.create_app()) as client:
        vital_response = client.post(
            "/procurement/supplier-prices",
            json={
                "business_id": "business-1",
                "supplier_name": "Vital",
                "items": [
                    {
                        "product_id": product.id,
                        "supplier_product_name": "Coca 2.25",
                        "cost_price": "1720",
                    }
                ],
            },
        )
        nini_response = client.post(
            "/procurement/supplier-prices",
            json={
                "business_id": "business-1",
                "supplier_name": "Nini",
                "items": [
                    {
                        "product_id": product.id,
                        "supplier_product_name": "Coca Cola 2.25L",
                        "cost_price": "1690",
                    }
                ],
            },
        )
        comparison_response = client.post(
            "/procurement/supplier-price-comparisons",
            json={
                "business_id": "business-1",
                "supplier_ids": [
                    vital_response.json()["supplier"]["id"],
                    nini_response.json()["supplier"]["id"],
                ],
                "needs": [{"product_id": product.id, "quantity": "12"}],
            },
        )

    assert vital_response.status_code == 200
    assert nini_response.status_code == 200
    assert comparison_response.status_code == 200
    row = comparison_response.json()["rows"][0]
    assert row["product_name"] == "COCA COLA 2.25L"
    assert row["best_supplier_name"] == "Nini"
    assert row["best_cost_price"] == "1690.00"
    assert row["estimated_savings_vs_system"] == "1920.00"
    assert [price["supplier_name"] for price in row["supplier_prices"]] == ["Nini", "Vital"]


def test_procurement_api_corrects_match_and_recalculates_recommendation(
    monkeypatch,
) -> None:
    session = build_procurement_session()
    repository = SqlProcurementRepository(session)
    document, candidate = seed_match_candidate(repository)
    replacement_product = repository.add_product(
        Product(
            business_id="business-1",
            external_product_id="pos-product-2",
            sku="UV2",
            name="UVITA PROMO TETRABRICK 1L",
            normalized_name="uvita promo tetrabrick 1l",
            current_cost=Decimal("1000"),
            sale_price=Decimal("1500"),
        )
    )
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)

    with TestClient(main.create_app()) as client:
        correction_response = client.post(
            f"/procurement/product-matches/{candidate.id}/correct",
            json={
                "business_id": "business-1",
                "product_id": replacement_product.id,
                "reviewed_by_user_id": "user-1",
            },
        )
        reviewed_response = client.get(
            f"/procurement/supplier-offers/{document.id}/matches",
            params={"business_id": "business-1"},
        )

    assert correction_response.status_code == 200
    assert correction_response.json()["accepted"] is True
    reviewed_candidate = reviewed_response.json()["candidates"][0]
    assert reviewed_candidate["product"]["id"] == replacement_product.id
    assert reviewed_candidate["candidate"]["status"] == "accepted"
    assert reviewed_candidate["candidate"]["cost_difference"] == "-469.00"
    assert reviewed_candidate["candidate"]["recommendation"] == "do_not_buy"


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


def test_procurement_api_lists_supplier_offer_document_summaries(monkeypatch) -> None:
    session = build_procurement_session()
    repository = SqlProcurementRepository(session)
    document, candidate = seed_match_candidate(repository)
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)

    with TestClient(main.create_app()) as client:
        client.post(
            f"/procurement/product-matches/{candidate.id}/accept",
            json={
                "business_id": "business-1",
                "reviewed_by_user_id": "user-1",
            },
        )
        response = client.get(
            "/procurement/supplier-offers/summaries",
            params={"business_id": "business-1"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body[0]["document"]["id"] == document.id
    assert body[0]["supplier_name"] == "Vital"
    assert body[0]["item_count"] == 1
    assert body[0]["candidate_count"] == 1
    assert body[0]["matched_count"] == 1
    assert body[0]["accepted_count"] == 1
    assert body[0]["pending_count"] == 0
    assert body[0]["buy_count"] == 1


def test_procurement_api_builds_supplier_offer_purchase_plan(monkeypatch, tmp_path) -> None:
    session = build_procurement_session()
    repository = SqlProcurementRepository(session)
    document, _ = seed_match_candidate(repository)
    sales_path = tmp_path / "ventasitems_2026-06-30_2026-07-30.csv"
    sales_path.write_text(
        "\n".join(
            [
                "id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto",
                "line-1;ticket-1;pos-product-1;2;5400;2088;2026-06-30T10:00:00.000Z;UV1",
                "line-2;ticket-2;pos-product-1;4;10800;2088;2026-07-01T10:00:00.000Z;UV1",
            ]
        ),
        encoding="utf-8",
    )
    PosSalesImportService(SqlSalesRepository(session)).import_csv(
        business_id="business-1",
        csv_path=sales_path,
    )
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)

    with TestClient(main.create_app()) as client:
        plan_response = client.get(
            f"/procurement/supplier-offers/{document.id}/purchase-plan",
            params={
                "business_id": "business-1",
                "date_from": "2026-06-30",
                "date_to": "2026-07-30",
            },
        )
        suggested_response = client.get(
            f"/procurement/supplier-offers/{document.id}/purchase-plan",
            params={
                "business_id": "business-1",
                "date_from": "2026-06-30",
                "date_to": "2026-07-30",
                "target_coverage_days": 7,
            },
        )

    assert plan_response.status_code == 200
    item = plan_response.json()["items"][0]
    assert item["supplier_offer_item"]["raw_name"] == "UVITA Vino t/b 1lt"
    assert item["sales_summary"]["quantity_sold"] == "6.000"
    assert item["sales_summary"]["average_daily_quantity"] == "0.19"
    assert item["suggested_quantity"] is None
    assert suggested_response.status_code == 200
    suggested_item = suggested_response.json()["items"][0]
    assert suggested_item["target_coverage_days"] == 7
    assert suggested_item["suggested_quantity"] == "1.330"


def test_procurement_api_purchase_plan_prioritizes_sales_with_positive_savings(
    monkeypatch,
    tmp_path,
) -> None:
    session = build_procurement_session()
    repository = SqlProcurementRepository(session)
    products = [
        repository.add_product(
            Product(
                business_id="business-1",
                external_product_id="pos-product-fast",
                sku="FAST",
                name="Producto muy vendido",
                normalized_name="producto muy vendido",
                current_cost=Decimal("1000"),
                sale_price=Decimal("1500"),
            )
        ),
        repository.add_product(
            Product(
                business_id="business-1",
                external_product_id="pos-product-cheap",
                sku="CHEAP",
                name="Producto barato proveedor",
                normalized_name="producto barato proveedor",
                current_cost=Decimal("2000"),
                sale_price=Decimal("2600"),
            )
        ),
        repository.add_product(
            Product(
                business_id="business-1",
                external_product_id="pos-product-expensive",
                sku="EXP",
                name="Producto proveedor caro",
                normalized_name="producto proveedor caro",
                current_cost=Decimal("900"),
                sale_price=Decimal("1300"),
            )
        ),
    ]
    supplier = repository.add_supplier(
        Supplier(
            business_id="business-1",
            name="Diarco",
            normalized_name="diarco",
        )
    )
    document = repository.add_supplier_offer_document(
        SupplierOfferDocument(
            business_id="business-1",
            supplier_id=supplier.id,
            source_filename="diarco.pdf",
        )
    )
    offer_items = repository.add_supplier_offer_items(
        [
            SupplierOfferItem(
                business_id="business-1",
                supplier_offer_document_id=document.id,
                supplier_id=supplier.id,
                raw_name="Producto muy vendido oferta",
                normalized_name="producto muy vendido oferta",
                offer_price=Decimal("800"),
            ),
            SupplierOfferItem(
                business_id="business-1",
                supplier_offer_document_id=document.id,
                supplier_id=supplier.id,
                raw_name="Producto barato proveedor oferta",
                normalized_name="producto barato proveedor oferta",
                offer_price=Decimal("1000"),
            ),
            SupplierOfferItem(
                business_id="business-1",
                supplier_offer_document_id=document.id,
                supplier_id=supplier.id,
                raw_name="Producto proveedor caro oferta",
                normalized_name="producto proveedor caro oferta",
                offer_price=Decimal("1100"),
            ),
        ]
    )
    repository.replace_product_match_candidates(
        business_id="business-1",
        supplier_offer_document_id=document.id,
        candidates=[
            ProductMatchCandidate(
                supplier_offer_item=offer_items[0],
                product=products[0],
                relationship_type=SupplierProductRelationship.EXACT_MATCH,
                confidence_score=Decimal("0.95"),
                reasons=[],
                cost_difference=Decimal("200"),
                recommendation="buy",
            ),
            ProductMatchCandidate(
                supplier_offer_item=offer_items[1],
                product=products[1],
                relationship_type=SupplierProductRelationship.EXACT_MATCH,
                confidence_score=Decimal("0.95"),
                reasons=[],
                cost_difference=Decimal("1000"),
                recommendation="buy",
            ),
            ProductMatchCandidate(
                supplier_offer_item=offer_items[2],
                product=products[2],
                relationship_type=SupplierProductRelationship.EXACT_MATCH,
                confidence_score=Decimal("0.95"),
                reasons=[],
                cost_difference=Decimal("-200"),
                recommendation="do_not_buy",
            ),
        ],
    )
    sales_path = tmp_path / "ventasitems_2026-06-30_2026-07-30.csv"
    sales_path.write_text(
        "\n".join(
            [
                "id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto",
                "line-1;ticket-1;pos-product-fast;30;45000;1000;2026-07-01T10:00:00.000Z;FAST",
                "line-2;ticket-2;pos-product-cheap;3;7800;2000;2026-07-01T10:00:00.000Z;CHEAP",
                "line-3;ticket-3;pos-product-expensive;60;78000;900;2026-07-01T10:00:00.000Z;EXP",
            ]
        ),
        encoding="utf-8",
    )
    PosSalesImportService(SqlSalesRepository(session)).import_csv(
        business_id="business-1",
        csv_path=sales_path,
    )
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)

    with TestClient(main.create_app()) as client:
        response = client.get(
            f"/procurement/supplier-offers/{document.id}/purchase-plan",
            params={
                "business_id": "business-1",
                "date_from": "2026-06-30",
                "date_to": "2026-07-30",
                "target_coverage_days": 7,
                "sort_by": "sales_then_savings",
                "only_positive_savings": "true",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["sort_by"] == "sales_then_savings"
    assert body["only_positive_savings"] is True
    assert [item["product"]["name"] for item in body["items"]] == [
        "Producto muy vendido",
        "Producto barato proveedor",
    ]
    assert body["items"][0]["candidate"]["cost_difference"] == "200.00"
    assert body["items"][0]["estimated_savings"] == "1358.00"

    with TestClient(main.create_app()) as client:
        pdf_response = client.get(
            f"/procurement/supplier-offers/{document.id}/purchase-plan.pdf",
            params={
                "business_id": "business-1",
                "date_from": "2026-06-30",
                "date_to": "2026-07-30",
                "target_coverage_days": 7,
                "sort_by": "sales_then_savings",
                "only_positive_savings": "true",
            },
        )

    assert pdf_response.status_code == 200
    assert pdf_response.headers["content-type"] == "application/pdf"
    assert pdf_response.content.startswith(b"%PDF")
    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(pdf_response.content)).pages
    )
    assert pdf_text.index("Producto muy vendido") < pdf_text.index("Producto barato proveedor")
    assert "Producto proveedor caro" not in pdf_text
    assert "Sugerido" not in pdf_text
    assert "Ahorro est." not in pdf_text
    assert "Reco." not in pdf_text


def test_procurement_ui_is_served(client) -> None:
    response = client.get("/procurement-ui", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/ui/index.html"


def test_procurement_ui_index_avoids_stale_assets(client) -> None:
    response = client.get("/ui/index.html")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "/ui/app.js?v=" in response.text


def test_chunk_extraction_metadata_survives_api_and_database(monkeypatch):
    from pypdf import PdfWriter

    from app.modules.procurement.schemas import (
        ExtractedSupplierOfferItem,
        SupplierOfferExtraction,
        SupplierOfferPageCoverage,
        SupplierOfferPageExtraction,
    )
    from app.providers.documents.dual import DualDocumentExtractionProvider

    class Reader:
        def extract_supplier_offer(self, *, document_path, supplier_hint=None, page_context=None):
            pages = []
            for number in range(1, page_context.page_count + 1):
                item = ExtractedSupplierOfferItem(
                    raw_name="Arroz Gallo 1 kg", brand="Gallo", offer_price=100, page_number=number
                )
                pages.append(
                    SupplierOfferPageExtraction(
                        page_number=number,
                        items=[item],
                        coverage=SupplierOfferPageCoverage(estimated_offer_blocks=1),
                    )
                )
            return SupplierOfferExtraction(
                supplier_name="Vital",
                page_extractions=pages,
                items=[item for page in pages for item in page.items],
            )

    session = build_procurement_session()
    repository = SqlProcurementRepository(session)
    repository.add_product(
        Product(
            business_id="business-1",
            name="Gallo Arroz 1000 g",
            normalized_name="gallo arroz 1000 g",
            brand="Gallo",
            unit_size=1000,
            unit="g",
            current_cost=200,
        )
    )
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)
    monkeypatch.setattr(
        main,
        "build_supplier_offer_document_provider",
        lambda name: DualDocumentExtractionProvider([("gemini", Reader()), ("openai", Reader())]),
    )
    stream = BytesIO()
    writer = PdfWriter()
    for _ in range(7):
        writer.add_blank_page(width=100, height=100)
    writer.write(stream)
    with TestClient(main.create_app()) as client:
        response = client.post(
            "/procurement/supplier-offers/from-document",
            data={"business_id": "business-1", "supplier_name": "Vital"},
            files={"file": ("original.pdf", stream.getvalue(), "application/pdf")},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    document_id = body["import_result"]["document"]["id"]
    document = repository.get_supplier_offer_document(
        business_id="business-1", supplier_offer_document_id=document_id
    )
    assert document.metadata["chunk_count"] == 2
    assert document.metadata["initial_provider_calls"] == 4
    assert document.metadata["page_count"] == 7
    assert document.metadata["agreed_item_count"] == 7
    assert document.metadata["retried_pages"] == []
    assert document.metadata["duration_seconds"] >= 0
    assert len(document.metadata["provider_attempts"]) == 2
    items = repository.list_supplier_offer_items(
        business_id="business-1", supplier_offer_document_id=document_id
    )
    assert len(items) == 7
    metadata = items[0].metadata
    assert metadata["agreement_score"] == 1
    assert metadata["agreement_status"] == "agreed_exact"
    assert metadata["selected_source"] in ("gemini", "openai")
    assert set(metadata["provider_observations"]) == {"gemini", "openai"}
    assert metadata["provider_observations"]["gemini"]["raw_name"] == "Arroz Gallo 1 kg"
    assert body["comparison"]["persisted_count"] == 7
    assert (
        repository.get_supplier_offer_document(
            business_id="other", supplier_offer_document_id=document_id
        )
        is None
    )


def test_completely_failed_pdf_is_persisted_for_human_review(monkeypatch):
    from pypdf import PdfWriter

    from app.providers.documents.dual import DualDocumentExtractionProvider

    class FailedReader:
        def extract_supplier_offer(self, **kwargs):
            raise TimeoutError("reader unavailable")

    session = build_procurement_session()
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "kapso_webhook_secret", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", session)
    monkeypatch.setattr(
        main,
        "build_supplier_offer_document_provider",
        lambda name: DualDocumentExtractionProvider(
            [("gemini", FailedReader()), ("openai", FailedReader())]
        ),
    )
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(stream)
    with TestClient(main.create_app()) as client:
        response = client.post(
            "/procurement/supplier-offers/from-document",
            data={"business_id": "business-1", "supplier_name": "Vital"},
            files={"file": ("failed.pdf", stream.getvalue(), "application/pdf")},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["import_result"]["items"] == []
    assert body["import_result"]["document"]["metadata"]["needs_review_pages"] == [1]
    assert body["import_result"]["document"]["metadata"]["retry_provider_calls"] == 2
    assert body["comparison"]["persisted_count"] == 0
