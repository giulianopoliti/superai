from app.modules.sales.schemas import ProductSalesSummary, PurchaseSuggestion


class PurchaseSuggestionService:
    def build_suggestions(
        self,
        *,
        sales_summaries: list[ProductSalesSummary],
        target_coverage_days: int,
        safety_days: int = 0,
        use_stock: bool = False,
        ignore_negative_stock: bool = True,
    ) -> list[PurchaseSuggestion]:
        return [
            PurchaseSuggestion(
                sales_summary=summary,
                target_coverage_days=target_coverage_days,
                safety_days=safety_days,
                use_stock=use_stock,
                ignore_negative_stock=ignore_negative_stock,
            )
            for summary in sales_summaries
        ]
