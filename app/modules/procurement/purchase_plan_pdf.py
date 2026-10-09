from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.modules.procurement.schemas import SupplierOfferPurchasePlan, SupplierOfferPurchasePlanItem


def build_supplier_offer_purchase_plan_pdf(plan: SupplierOfferPurchasePlan) -> bytes:
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Plan de compra", styles["Title"]),
        Paragraph(
            (
                f"Ventas: {plan.date_from.isoformat()} a {plan.date_to.isoformat()} - "
                f"Orden: {_sort_label(plan.sort_by)} - "
                f"Solo ahorro positivo: {'si' if plan.only_positive_savings else 'no'}"
            ),
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]
    rows = [
        [
            "Oferta proveedor",
            "Costo unit.",
            "Producto catalogo",
            "Costo actual",
            "Ahorro",
            "Vendido",
        ]
    ]
    rows.extend(_item_row(item) for item in plan.items)
    table = Table(
        rows,
        repeatRows=1,
        colWidths=[250, 72, 250, 72, 68, 68],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174c4f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("FONTSIZE", (0, 1), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9e0e4")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fa")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    story.append(table)
    document.build(story)
    return buffer.getvalue()


def _item_row(item: SupplierOfferPurchasePlanItem) -> list[str]:
    summary = item.sales_summary
    return [
        item.supplier_offer_item.raw_name,
        _money(item.supplier_offer_item.comparable_unit_price),
        item.product.name if item.product else "Sin match",
        _money(item.product.current_cost if item.product else None),
        _money(item.candidate.cost_difference),
        _quantity(summary.quantity_sold if summary else None),
    ]


def _money(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _quantity(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value:,.3f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _sort_label(value: str) -> str:
    if value == "sales_then_savings":
        return "mas vendidos + ahorro"
    if value == "estimated_savings":
        return "ahorro estimado"
    if value == "savings_then_sales":
        return "mayor ahorro unitario"
    if value == "supplier_order":
        return "orden proveedor"
    return value
