from typing import Protocol

from app.assistant.tools.schemas import (
    AssistantToolDefinition,
    AssistantToolName,
    AssistantToolResult,
    ComparePeriodsQuery,
    ProductHistoryQuery,
    ResolveFamiliesQuery,
    ResolveProductsQuery,
    SalesSummaryQuery,
    SearchProductsQuery,
    StockStatusQuery,
    ToolExecutionContext,
    TopProductsQuery,
)

TOOL_DESCRIPTIONS: dict[AssistantToolName, str] = {
    AssistantToolName.SALES_SUMMARY: (
        "Totales de ventas, facturación, tickets únicos y ticket promedio con filtros."
    ),
    AssistantToolName.SALES_PRODUCT_HISTORY: (
        "Ventas de un producto por día; requiere nombre inequívoco o SKU."
    ),
    AssistantToolName.SALES_COMPARE_PERIODS: (
        "Compara dos períodos; por defecto contra el período anterior de igual duración."
    ),
    AssistantToolName.CATALOG_SEARCH_PRODUCTS: (
        "Busca productos, precios y costos en el catálogo importado."
    ),
    AssistantToolName.INVENTORY_STOCK_STATUS: (
        "Consulta stock del catálogo importado; max_stock=0 muestra stock cero o negativo."
    ),
    AssistantToolName.CATALOG_RESOLVE_FAMILIES: (
        "Resuelve nombres de familias/categorias del usuario "
        "contra las familias reales del catalogo."
    ),
    AssistantToolName.CATALOG_RESOLVE_PRODUCTS: (
        "Resuelve nombres aproximados, SKUs o codigos de productos contra productos reales."
    ),
    AssistantToolName.SALES_TOP_PRODUCTS: (
        "Consulta el ranking de productos vendidos por cantidad, facturacion o margen."
    ),
}

TOOL_INPUT_SCHEMAS = {
    AssistantToolName.SALES_SUMMARY: SalesSummaryQuery,
    AssistantToolName.SALES_PRODUCT_HISTORY: ProductHistoryQuery,
    AssistantToolName.SALES_COMPARE_PERIODS: ComparePeriodsQuery,
    AssistantToolName.CATALOG_SEARCH_PRODUCTS: SearchProductsQuery,
    AssistantToolName.INVENTORY_STOCK_STATUS: StockStatusQuery,
    AssistantToolName.CATALOG_RESOLVE_FAMILIES: ResolveFamiliesQuery,
    AssistantToolName.CATALOG_RESOLVE_PRODUCTS: ResolveProductsQuery,
    AssistantToolName.SALES_TOP_PRODUCTS: TopProductsQuery,
}


class AssistantTool(Protocol):
    name: AssistantToolName

    def execute(
        self,
        *,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> AssistantToolResult:
        pass


class AssistantToolRegistry:
    def __init__(self, tools: list[AssistantTool] | None = None) -> None:
        self._tools: dict[AssistantToolName, AssistantTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: AssistantTool) -> None:
        self._tools[tool.name] = tool

    def definitions(self) -> list[AssistantToolDefinition]:
        return [
            AssistantToolDefinition(
                name=name,
                description=TOOL_DESCRIPTIONS.get(name, name.value),
                input_schema=TOOL_INPUT_SCHEMAS[name].model_json_schema()
                if name in TOOL_INPUT_SCHEMAS
                else {},
                read_only=True,
                requires_confirmation=False,
            )
            for name in self._tools
        ]

    def has_tool(self, name: AssistantToolName) -> bool:
        return name in self._tools

    def execute(
        self,
        *,
        name: AssistantToolName,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> AssistantToolResult:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Assistant tool is not registered: {name}")
        if arguments.get("business_id") not in (None, context.business_id):
            raise ValueError("business_id must match execution context")
        return tool.execute(arguments=arguments, context=context)
