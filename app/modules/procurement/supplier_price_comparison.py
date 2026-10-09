from decimal import Decimal

from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.normalization import normalize_text
from app.modules.procurement.pricing import (
    SupplierPriceType,
    is_likely_pack_price,
    normalize_offer_price,
)
from app.modules.procurement.schemas import (
    ProductSupplierPrice,
    Supplier,
    SupplierPriceComparisonCell,
    SupplierPriceComparisonRequest,
    SupplierPriceComparisonResponse,
    SupplierPriceComparisonRow,
    SupplierPriceUpsertRequest,
    SupplierPriceUpsertResponse,
    SupplierProduct,
    utc_now,
)


class SupplierPriceComparisonService:
    def __init__(self, repository: SqlProcurementRepository) -> None:
        self._repository = repository

    def record_supplier_prices(
        self,
        request: SupplierPriceUpsertRequest,
    ) -> SupplierPriceUpsertResponse:
        if not request.items:
            raise ValueError("At least one supplier price item is required.")

        supplier = self._repository.upsert_supplier(
            Supplier(
                business_id=request.business_id,
                name=request.supplier_name.strip(),
                normalized_name=normalize_text(request.supplier_name),
            )
        )
        saved_prices: list[SupplierProduct] = []
        for item in request.items:
            product = self._repository.get_product(
                business_id=request.business_id,
                product_id=item.product_id,
            )
            if product is None:
                raise ValueError(f"Product not found: {item.product_id}")

            supplier_product_name = item.supplier_product_name or product.name
            saved_prices.append(
                self._repository.add_supplier_product(
                    SupplierProduct(
                        business_id=request.business_id,
                        supplier_id=supplier.id,
                        product_id=product.id,
                        supplier_product_name=supplier_product_name,
                        supplier_product_normalized_name=normalize_text(supplier_product_name),
                        cost_price=item.cost_price,
                        currency=item.currency,
                        tax_included=item.tax_included,
                        package_quantity=item.package_quantity,
                        price_type=item.price_type,
                        observed_at=item.observed_at or utc_now(),
                        metadata=item.metadata,
                    )
                )
            )
        return SupplierPriceUpsertResponse(supplier=supplier, prices=saved_prices)

    def compare_supplier_prices(
        self,
        request: SupplierPriceComparisonRequest,
    ) -> SupplierPriceComparisonResponse:
        needs_by_product_id = {need.product_id: need.quantity for need in request.needs}
        product_ids = [*request.product_ids]
        for product_id in needs_by_product_id:
            if product_id not in product_ids:
                product_ids.append(product_id)
        if not product_ids:
            raise ValueError("At least one product_id or need is required.")

        supplier_filter = set(request.supplier_ids)
        rows: list[SupplierPriceComparisonRow] = []
        for product_id in product_ids:
            comparison = self._repository.compare_supplier_prices(
                business_id=request.business_id,
                product_id=product_id,
            )
            if comparison is None:
                raise ValueError(f"Product not found: {product_id}")

            latest_prices = self._latest_price_per_supplier(comparison.supplier_prices)
            if supplier_filter:
                latest_prices = [
                    price for price in latest_prices if price.supplier_id in supplier_filter
                ]

            comparable_prices = [
                price
                for price in latest_prices
                if self._effective_unit_cost(price, comparison.current_cost)[0] is not None
            ]
            best_price = min(
                comparable_prices,
                key=lambda price: self._effective_unit_cost(
                    price,
                    comparison.current_cost,
                )[0]
                or Decimal("Infinity"),
                default=None,
            )
            best_unit_cost = (
                self._effective_unit_cost(best_price, comparison.current_cost)[0]
                if best_price
                else None
            )
            quantity = needs_by_product_id.get(product_id)
            cells = [
                self._build_cell(
                    price=price,
                    current_cost=comparison.current_cost,
                    best_price=best_price,
                )
                for price in latest_prices
            ]
            rows.append(
                SupplierPriceComparisonRow(
                    product_id=comparison.product_id,
                    product_name=comparison.product_name,
                    current_cost=comparison.current_cost,
                    sale_price=comparison.sale_price,
                    quantity=quantity,
                    supplier_prices=cells,
                    missing_supplier_ids=[
                        supplier_id
                        for supplier_id in request.supplier_ids
                        if supplier_id not in {price.supplier_id for price in latest_prices}
                    ],
                    best_supplier_id=best_price.supplier_id if best_price else None,
                    best_supplier_name=best_price.supplier_name if best_price else None,
                    best_cost_price=best_unit_cost,
                    estimated_best_total=self._estimated_total(best_unit_cost, quantity),
                    estimated_savings_vs_system=self._estimated_savings(
                        current_cost=comparison.current_cost,
                        best_unit_cost=best_unit_cost,
                        quantity=quantity,
                    ),
                )
            )

        return SupplierPriceComparisonResponse(
            business_id=request.business_id,
            supplier_ids=request.supplier_ids,
            rows=rows,
        )

    @staticmethod
    def _latest_price_per_supplier(
        prices: list[ProductSupplierPrice],
    ) -> list[ProductSupplierPrice]:
        latest_by_supplier: dict[str, ProductSupplierPrice] = {}
        for price in prices:
            current = latest_by_supplier.get(price.supplier_id)
            if current is None or price.observed_at > current.observed_at:
                latest_by_supplier[price.supplier_id] = price
        return sorted(
            latest_by_supplier.values(),
            key=lambda price: (
                price.comparable_unit_cost is None,
                price.comparable_unit_cost or Decimal("Infinity"),
            ),
        )

    @staticmethod
    def _build_cell(
        *,
        price: ProductSupplierPrice,
        current_cost: Decimal | None,
        best_price: ProductSupplierPrice | None,
    ) -> SupplierPriceComparisonCell:
        effective_price = SupplierPriceComparisonService._effective_unit_cost
        comparable_unit_cost, price_was_inferred = effective_price(price, current_cost)
        unit_savings = None
        if current_cost is not None and comparable_unit_cost is not None:
            unit_savings = (current_cost - comparable_unit_cost).quantize(Decimal("0.01"))
        return SupplierPriceComparisonCell(
            supplier_product_id=price.supplier_product_id,
            supplier_id=price.supplier_id,
            supplier_name=price.supplier_name,
            supplier_product_name=price.supplier_product_name,
            cost_price=comparable_unit_cost,
            quoted_cost_price=price.cost_price,
            currency=price.currency,
            tax_included=price.tax_included,
            package_quantity=price.package_quantity,
            price_type=price.price_type,
            price_was_inferred=price_was_inferred,
            observed_at=price.observed_at,
            is_best=best_price is not None and price.supplier_id == best_price.supplier_id,
            unit_savings_vs_system=unit_savings,
        )

    @staticmethod
    def _estimated_total(
        best_unit_cost: Decimal | None,
        quantity: Decimal | None,
    ) -> Decimal | None:
        if best_unit_cost is None or quantity is None:
            return None
        return (best_unit_cost * quantity).quantize(Decimal("0.01"))

    @staticmethod
    def _estimated_savings(
        *,
        current_cost: Decimal | None,
        best_unit_cost: Decimal | None,
        quantity: Decimal | None,
    ) -> Decimal | None:
        if current_cost is None or best_unit_cost is None or quantity is None:
            return None
        return ((current_cost - best_unit_cost) * quantity).quantize(Decimal("0.01"))

    @staticmethod
    def _effective_unit_cost(
        price: ProductSupplierPrice,
        current_cost: Decimal | None,
    ) -> tuple[Decimal | None, bool]:
        if price.price_type in {SupplierPriceType.UNIT, SupplierPriceType.UNKNOWN} and (
            is_likely_pack_price(
                offer_price=price.cost_price,
                current_unit_cost=current_cost,
                package_quantity=price.package_quantity,
            )
        ):
            inferred_price = normalize_offer_price(
                offer_price=price.cost_price,
                package_quantity=price.package_quantity,
                price_type=SupplierPriceType.PACK,
            )
            if inferred_price is not None:
                return inferred_price, True
        return price.comparable_unit_cost, False
