import re
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.modules.procurement.identity import AUTO_ACCEPT_REASON
from app.modules.procurement.schemas import (
    ProductMatchCandidateStatus,
    SupplierOfferPurchasePlan,
    SupplierOfferPurchasePlanItem,
    SupplierPriceType,
)


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
    cell_style = ParagraphStyle("PurchasePlanCell", fontSize=8, leading=10)
    story = [
        Paragraph("Plan de compra", styles["Title"]),
        Paragraph(
            (
                f"Ventas: {plan.date_from:%d/%m/%Y} a {plan.date_to:%d/%m/%Y} - "
                f"Orden: {_sort_label(plan.sort_by)} - "
                f"Solo ahorro positivo: {'sí' if plan.only_positive_savings else 'no'}"
            ),
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]
    rows = [
        [
            "Oferta del proveedor",
            "Precio del folleto",
            "Producto del catálogo",
            "Vínculo",
            "Costo actual",
            "Ahorro",
            "Unidades vendidas",
        ]
    ]
    rows.extend(_item_row(item, cell_style) for item in plan.items)
    table = Table(
        rows,
        repeatRows=1,
        colWidths=[174, 112, 172, 100, 66, 65, 88],
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
                ("ALIGN", (4, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    story.append(table)
    document.build(story)
    return buffer.getvalue()


def _item_row(item: SupplierOfferPurchasePlanItem, style: ParagraphStyle) -> list[str | Paragraph]:
    offer = item.supplier_offer_item
    candidate = item.candidate
    accepted = candidate.status == ProductMatchCandidateStatus.ACCEPTED
    rejected = candidate.status == ProductMatchCandidateStatus.REJECTED
    product = None if rejected else item.product
    if accepted and product:
        status = "Automático" if AUTO_ACCEPT_REASON in candidate.reasons else "Aceptado manualmente"
    elif rejected:
        status = "Rechazado"
    elif product:
        status = Paragraph('<font color="#5b6268" size="7">Pendiente de aceptar</font>', style)
    else:
        status = "Sin coincidencia"

    price_lines = [_money(offer.offer_price)]
    unit_price = offer.comparable_unit_price
    if unit_price is None:
        price_lines.append("Base a revisar")
    elif offer.price_type == SupplierPriceType.PACK:
        price_lines.append(f"{_money(unit_price)} por unidad")
    analysis = offer.metadata.get("analysis_v2")
    if isinstance(analysis, dict):
        interpretation = analysis.get("interpretation")
        if isinstance(interpretation, dict):
            minimum = interpretation.get("minimum_purchase")
            refs = minimum.get("refs") if isinstance(minimum, dict) else None
            if isinstance(refs, list) and refs and isinstance(refs[0], dict):
                condition = refs[0].get("quote")
                if isinstance(condition, str) and condition.strip():
                    # Some readers cite the entire card for a purchase minimum.
                    minimum_label = re.search(r"\bdesde\s+\d+\s+unidades\b|\bllevando por display\b", condition, re.I)
                    if minimum_label:
                        price_lines.append(" ".join(minimum_label.group().split()))
                    else:
                        price_lines.append(condition.strip().splitlines()[0])

    return [
        Paragraph(escape(offer.raw_name), style),
        Paragraph("<br/>".join(escape(line) for line in price_lines), style),
        Paragraph(escape(product.name), style) if product else "—",
        status,
        _money(product.current_cost) if product else "—",
        _money(candidate.cost_difference) if accepted and product else "—",
        _quantity(item.sales_summary.quantity_sold) if product and item.sales_summary else "—",
    ]


def _money(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _quantity(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value:,.3f}".rstrip("0").rstrip(".").replace(",", "X").replace(".", ",").replace("X", ".")


def _sort_label(value: str) -> str:
    if value == "sales_then_savings":
        return "más vendidos + ahorro"
    if value == "estimated_savings":
        return "ahorro estimado"
    if value == "savings_then_sales":
        return "mayor ahorro unitario"
    if value == "supplier_order":
        return "orden proveedor"
    return value
