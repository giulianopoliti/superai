from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.pricing import SupplierPriceType
from app.modules.procurement.schemas import (
    Product,
    SupplierPriceComparisonNeed,
    SupplierPriceComparisonRequest,
    SupplierPriceUpsertItem,
    SupplierPriceUpsertRequest,
)
from app.modules.procurement.supplier_price_comparison import SupplierPriceComparisonService


def build_repository() -> SqlProcurementRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)
    return SqlProcurementRepository(session)


def test_supplier_price_comparison_uses_latest_price_per_supplier() -> None:
    repository = build_repository()
    service = SupplierPriceComparisonService(repository)
    product = repository.add_product(
        Product(
            business_id="business-1",
            name="COCA COLA 2.25L",
            normalized_name="coca cola 2.25l",
            current_cost=Decimal("1850"),
            sale_price=Decimal("2400"),
        )
    )

    vital = service.record_supplier_prices(
        SupplierPriceUpsertRequest(
            business_id="business-1",
            supplier_name="Vital",
            items=[
                SupplierPriceUpsertItem(
                    product_id=product.id,
                    cost_price=Decimal("1720"),
                    observed_at=datetime(2026, 8, 10, tzinfo=UTC),
                ),
                SupplierPriceUpsertItem(
                    product_id=product.id,
                    cost_price=Decimal("1710"),
                    observed_at=datetime(2026, 8, 13, tzinfo=UTC),
                ),
            ],
        )
    )
    nini = service.record_supplier_prices(
        SupplierPriceUpsertRequest(
            business_id="business-1",
            supplier_name="Nini",
            items=[
                SupplierPriceUpsertItem(
                    product_id=product.id,
                    cost_price=Decimal("1690"),
                    observed_at=datetime(2026, 8, 12, tzinfo=UTC),
                )
            ],
        )
    )

    response = service.compare_supplier_prices(
        SupplierPriceComparisonRequest(
            business_id="business-1",
            supplier_ids=[vital.supplier.id, nini.supplier.id],
            needs=[
                SupplierPriceComparisonNeed(
                    product_id=product.id,
                    quantity=Decimal("12"),
                )
            ],
        )
    )

    assert response.compared_product_count == 1
    row = response.rows[0]
    assert row.best_supplier_name == "Nini"
    assert row.best_cost_price == Decimal("1690.00")
    assert row.estimated_best_total == Decimal("20280.00")
    assert row.estimated_savings_vs_system == Decimal("1920.00")
    assert [(price.supplier_name, price.cost_price) for price in row.supplier_prices] == [
        ("Nini", Decimal("1690.00")),
        ("Vital", Decimal("1710.00")),
    ]
    assert row.supplier_prices[0].is_best is True
    assert row.supplier_prices[1].unit_savings_vs_system == Decimal("140.00")


def test_supplier_price_comparison_reports_missing_selected_supplier() -> None:
    repository = build_repository()
    service = SupplierPriceComparisonService(repository)
    product = repository.add_product(
        Product(
            business_id="business-1",
            name="YERBA 1KG",
            normalized_name="yerba 1kg",
            current_cost=Decimal("3200"),
        )
    )
    vital = service.record_supplier_prices(
        SupplierPriceUpsertRequest(
            business_id="business-1",
            supplier_name="Vital",
            items=[
                SupplierPriceUpsertItem(
                    product_id=product.id,
                    cost_price=Decimal("3100"),
                )
            ],
        )
    )

    response = service.compare_supplier_prices(
        SupplierPriceComparisonRequest(
            business_id="business-1",
            product_ids=[product.id],
            supplier_ids=[vital.supplier.id, "missing-supplier"],
        )
    )

    assert response.rows[0].best_supplier_name == "Vital"
    assert response.rows[0].missing_supplier_ids == ["missing-supplier"]


def test_supplier_price_comparison_uses_normalized_pack_cost() -> None:
    repository = build_repository()
    service = SupplierPriceComparisonService(repository)
    product = repository.add_product(
        Product(
            business_id="business-1",
            name="COCA COLA ZERO 2.25L",
            normalized_name="coca cola zero 2 25l",
            current_cost=Decimal("2150"),
        )
    )

    vital = service.record_supplier_prices(
        SupplierPriceUpsertRequest(
            business_id="business-1",
            supplier_name="Vital",
            items=[
                SupplierPriceUpsertItem(
                    product_id=product.id,
                    cost_price=Decimal("12000"),
                    package_quantity=6,
                    price_type=SupplierPriceType.PACK,
                )
            ],
        )
    )
    nini = service.record_supplier_prices(
        SupplierPriceUpsertRequest(
            business_id="business-1",
            supplier_name="Nini",
            items=[
                SupplierPriceUpsertItem(
                    product_id=product.id,
                    cost_price=Decimal("2050"),
                    price_type=SupplierPriceType.UNIT,
                )
            ],
        )
    )

    response = service.compare_supplier_prices(
        SupplierPriceComparisonRequest(
            business_id="business-1",
            product_ids=[product.id],
            supplier_ids=[vital.supplier.id, nini.supplier.id],
        )
    )

    row = response.rows[0]
    assert row.best_supplier_name == "Vital"
    assert row.best_cost_price == Decimal("2000.00")
    assert row.supplier_prices[0].quoted_cost_price == Decimal("12000.00")
    assert row.supplier_prices[0].cost_price == Decimal("2000.00")


def test_supplier_price_comparison_does_not_rank_unknown_price_basis() -> None:
    repository = build_repository()
    service = SupplierPriceComparisonService(repository)
    product = repository.add_product(
        Product(
            business_id="business-1",
            name="COCA COLA ZERO 2.25L",
            normalized_name="coca cola zero 2 25l",
            current_cost=None,
        )
    )

    supplier = service.record_supplier_prices(
        SupplierPriceUpsertRequest(
            business_id="business-1",
            supplier_name="Vital",
            items=[
                SupplierPriceUpsertItem(
                    product_id=product.id,
                    cost_price=Decimal("12000"),
                    package_quantity=6,
                    price_type=SupplierPriceType.UNKNOWN,
                )
            ],
        )
    )

    response = service.compare_supplier_prices(
        SupplierPriceComparisonRequest(
            business_id="business-1",
            product_ids=[product.id],
            supplier_ids=[supplier.supplier.id],
        )
    )

    row = response.rows[0]
    assert row.best_supplier_id is None
    assert row.supplier_prices[0].cost_price is None
    assert row.supplier_prices[0].quoted_cost_price == Decimal("12000.00")


def test_supplier_price_comparison_infers_pack_price_from_cost_gap() -> None:
    repository = build_repository()
    service = SupplierPriceComparisonService(repository)
    product = repository.add_product(
        Product(
            business_id="business-1",
            name="COCA COLA ZERO 2.25L",
            normalized_name="coca cola zero 2 25l",
            current_cost=Decimal("2150"),
        )
    )
    supplier = service.record_supplier_prices(
        SupplierPriceUpsertRequest(
            business_id="business-1",
            supplier_name="Vital",
            items=[
                SupplierPriceUpsertItem(
                    product_id=product.id,
                    cost_price=Decimal("12000"),
                    package_quantity=6,
                )
            ],
        )
    )

    response = service.compare_supplier_prices(
        SupplierPriceComparisonRequest(
            business_id="business-1",
            product_ids=[product.id],
            supplier_ids=[supplier.supplier.id],
        )
    )

    row = response.rows[0]
    assert row.best_cost_price == Decimal("2000.00")
    assert row.supplier_prices[0].cost_price == Decimal("2000.00")
    assert row.supplier_prices[0].price_was_inferred is True
