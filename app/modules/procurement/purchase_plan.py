from datetime import date
from decimal import Decimal

from app.db.repositories.sql_sales import SqlSalesRepository
from app.modules.procurement.review import ProductMatchReviewService
from app.modules.procurement.schemas import (
    ProductMatchReviewCandidate,
    SupplierOfferPurchasePlan,
    SupplierOfferPurchasePlanItem,
)
from app.modules.sales.schemas import ProductSalesSummary


class SupplierOfferPurchasePlanService:
    def __init__(
        self,
        *,
        review_service: ProductMatchReviewService,
        sales_repository: SqlSalesRepository,
    ) -> None:
        self._review_service = review_service
        self._sales_repository = sales_repository

    def build_plan(
        self,
        *,
        business_id: str,
        supplier_offer_document_id: str,
        date_from: date,
        date_to: date,
        target_coverage_days: int | None = None,
        use_stock: bool = False,
        ignore_negative_stock: bool = True,
        sort_by: str = "sales_then_savings",
        only_positive_savings: bool = False,
    ) -> SupplierOfferPurchasePlan:
        review_list = self._review_service.list_candidates(
            business_id=business_id,
            supplier_offer_document_id=supplier_offer_document_id,
        )
        product_ids = {
            candidate.product.id
            for candidate in review_list.candidates
            if candidate.product is not None
        }
        sales_summaries = self._sales_repository.summarize_products_sales(
            business_id=business_id,
            product_ids=product_ids,
            date_from=date_from,
            date_to=date_to,
        )
        items = [
            self._build_item(
                candidate=candidate,
                sales_summaries=sales_summaries,
                target_coverage_days=target_coverage_days,
                use_stock=use_stock,
                ignore_negative_stock=ignore_negative_stock,
            )
            for candidate in review_list.candidates
        ]
        if only_positive_savings:
            items = [item for item in items if self._unit_savings(item) > 0]
        items = self._sort_items(items=items, sort_by=sort_by)
        return SupplierOfferPurchasePlan(
            business_id=business_id,
            supplier_offer_document_id=supplier_offer_document_id,
            date_from=date_from,
            date_to=date_to,
            target_coverage_days=target_coverage_days,
            use_stock=use_stock,
            ignore_negative_stock=ignore_negative_stock,
            sort_by=sort_by,
            only_positive_savings=only_positive_savings,
            items=items,
        )

    def _build_item(
        self,
        *,
        candidate: ProductMatchReviewCandidate,
        sales_summaries: dict[str, ProductSalesSummary],
        target_coverage_days: int | None,
        use_stock: bool,
        ignore_negative_stock: bool,
    ) -> SupplierOfferPurchasePlanItem:
        sales_summary = None
        suggested_quantity = None
        if candidate.product is not None:
            sales_summary = sales_summaries.get(candidate.product.id)
        if sales_summary is not None and target_coverage_days is not None:
            demand_quantity = sales_summary.average_daily_quantity * Decimal(target_coverage_days)
            stock_deduction = Decimal("0")
            if use_stock and sales_summary.current_stock is not None:
                stock_deduction = sales_summary.current_stock
                if ignore_negative_stock:
                    stock_deduction = max(stock_deduction, Decimal("0"))
            suggested_quantity = max(demand_quantity - stock_deduction, Decimal("0")).quantize(
                Decimal("0.001")
            )
        estimated_savings = None
        unit_savings = candidate.candidate.cost_difference
        if (
            unit_savings is not None
            and unit_savings > 0
            and suggested_quantity is not None
            and suggested_quantity > 0
        ):
            estimated_savings = (unit_savings * suggested_quantity).quantize(Decimal("0.01"))
        return SupplierOfferPurchasePlanItem(
            candidate=candidate.candidate,
            supplier_offer_item=candidate.supplier_offer_item,
            product=candidate.product,
            sales_summary=sales_summary,
            target_coverage_days=target_coverage_days,
            suggested_quantity=suggested_quantity,
            estimated_savings=estimated_savings,
        )

    @staticmethod
    def _unit_savings(item: SupplierOfferPurchasePlanItem) -> Decimal:
        return item.candidate.cost_difference or Decimal("0")

    @staticmethod
    def _quantity_sold(item: SupplierOfferPurchasePlanItem) -> Decimal:
        if item.sales_summary is None:
            return Decimal("0")
        return item.sales_summary.quantity_sold

    @staticmethod
    def _average_daily_quantity(item: SupplierOfferPurchasePlanItem) -> Decimal:
        if item.sales_summary is None:
            return Decimal("0")
        return item.sales_summary.average_daily_quantity

    @staticmethod
    def _estimated_savings(item: SupplierOfferPurchasePlanItem) -> Decimal:
        return item.estimated_savings or Decimal("0")

    def _sort_items(
        self,
        *,
        items: list[SupplierOfferPurchasePlanItem],
        sort_by: str,
    ) -> list[SupplierOfferPurchasePlanItem]:
        if sort_by == "savings_then_sales":
            return sorted(
                items,
                key=lambda item: (
                    self._unit_savings(item) > 0,
                    self._unit_savings(item),
                    self._quantity_sold(item),
                    self._average_daily_quantity(item),
                ),
                reverse=True,
            )
        if sort_by == "estimated_savings":
            return sorted(
                items,
                key=lambda item: (
                    self._estimated_savings(item),
                    self._quantity_sold(item),
                    self._unit_savings(item),
                ),
                reverse=True,
            )
        if sort_by == "supplier_order":
            return items
        return sorted(
            items,
            key=lambda item: (
                self._unit_savings(item) > 0,
                self._quantity_sold(item),
                self._unit_savings(item),
                self._average_daily_quantity(item),
            ),
            reverse=True,
        )
