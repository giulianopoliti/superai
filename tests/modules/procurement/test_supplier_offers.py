from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.schemas import (
    ExtractedSupplierOfferItem,
    SupplierOfferDocumentStatus,
    SupplierOfferExtraction,
)
from app.modules.procurement.supplier_offers import SupplierOfferService


def build_repository() -> SqlProcurementRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)
    return SqlProcurementRepository(session)


def test_supplier_offer_service_creates_supplier_document_and_items() -> None:
    repository = build_repository()
    service = SupplierOfferService(repository)

    result = service.create_manual_offer(
        business_id="business-1",
        supplier_name="Vital",
        source_filename="vital-test.txt",
        raw_text="CAÑUELAS ACEITE 900ML $2990",
        items=[
            {
                "raw_name": "CAÑUELAS ACEITE 900ML",
                "brand": "CAÑUELAS",
                "offer_price": "2990",
                "page_number": 1,
                "confidence_score": "0.95",
            },
            {
                "raw_name": "ARROZ OKITA 1KG",
                "brand": "OKITA",
                "offer_price": "710",
                "page_number": 1,
            },
        ],
    )

    assert result.document.extraction_status == SupplierOfferDocumentStatus.EXTRACTED
    assert result.document.metadata == {"item_count": 2}
    assert [item.raw_name for item in result.items] == [
        "CAÑUELAS ACEITE 900ML",
        "ARROZ OKITA 1KG",
    ]
    assert result.items[0].offer_price == Decimal("2990.00")
    assert result.items[0].unit_size == Decimal("900.000")
    assert result.items[0].unit == "ml"

    saved_items = repository.list_supplier_offer_items(
        business_id="business-1",
        supplier_offer_document_id=result.document.id,
    )
    assert len(saved_items) == 2
    assert repository.list_supplier_offer_items(
        business_id="business-2",
        supplier_offer_document_id=result.document.id,
    ) == []


def test_supplier_upsert_reuses_supplier_for_same_business() -> None:
    repository = build_repository()
    service = SupplierOfferService(repository)

    first = service.upsert_supplier(business_id="business-1", supplier_name="Vital")
    second = service.upsert_supplier(business_id="business-1", supplier_name=" vital ")
    other_business = service.upsert_supplier(business_id="business-2", supplier_name="Vital")

    assert second.id == first.id
    assert other_business.id != first.id


def test_supplier_offer_service_persists_extraction_result() -> None:
    repository = build_repository()
    service = SupplierOfferService(repository)
    extraction = SupplierOfferExtraction(
        supplier_name="Vital",
        source_filename="oferta-vital.txt",
        raw_text="CAÑUELAS ACEITE 900ML $2990",
        warnings=["low image quality on page 1"],
        items=[
            ExtractedSupplierOfferItem(
                raw_name="CAÑUELAS ACEITE 900ML",
                brand="CAÑUELAS",
                unit_size=Decimal("900"),
                unit="ml",
                offer_price=Decimal("2990"),
                confidence_score=Decimal("0.95"),
                notes="clear row",
            )
        ],
    )

    result = service.create_offer_from_extraction(
        business_id="business-1",
        supplier_name=extraction.supplier_name or "Vital",
        extraction=extraction,
    )

    assert result.document.document_type == "extracted"
    assert result.document.extraction_provider == "document_extraction"
    assert result.items[0].unit_size == Decimal("900.000")
    assert result.items[0].metadata == {
        "price_type": "unit",
        "notes": "clear row",
        "document_warnings": ["low image quality on page 1"],
    }
