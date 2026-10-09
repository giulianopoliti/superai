import csv
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from app.db.repositories.sql_sales import SqlSalesRepository
from app.modules.procurement.normalization import parse_argentine_decimal
from app.modules.sales.schemas import SalesImportResult, SalesImportStatus, SalesItemLine

CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
SALES_FILENAME_PATTERN = re.compile(
    r"ventasitems_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
REQUIRED_COLUMNS = {
    "id",
    "idventa",
    "idproducto",
    "scodproducto",
    "rcantidad",
    "rtotal",
    "rcostou",
    "fcreacion",
}


class PosSalesImportService:
    def __init__(self, repository: SqlSalesRepository) -> None:
        self._repository = repository

    def import_csv(self, *, business_id: str, csv_path: Path) -> SalesImportResult:
        period_start, period_end = self._period_from_filename(csv_path.name)
        result = SalesImportResult(
            business_id=business_id,
            source_filename=csv_path.name,
            period_start=period_start,
            period_end=period_end,
        )
        result = self._repository.add_sales_import(result)

        tickets: set[str] = set()
        products: set[str] = set()
        dates: set[date] = set()
        external_product_ids: set[str] = set()
        parsed_rows: list[_ParsedSalesRow] = []

        try:
            with self._open_csv(csv_path) as file:
                csv_encoding = file.encoding
                reader = csv.DictReader(file, delimiter=";")
                self._validate_headers(reader.fieldnames)
                for line_number, row in enumerate(reader, start=2):
                    result.row_count += 1
                    parsed = self._parse_row(row)
                    if parsed is None:
                        result.skipped_count += 1
                        result.errors.append(f"line {line_number}: invalid sales row")
                        continue

                    tickets.add(parsed.ticket_id)
                    products.add(parsed.external_product_id)
                    dates.add(parsed.sale_date)
                    external_product_ids.add(parsed.external_product_id)
                    parsed_rows.append(parsed)

            product_ids = self._repository.resolve_product_ids(
                business_id=business_id,
                external_product_ids=external_product_ids,
            )
            sales_lines = [
                parsed.to_line(
                    business_id=business_id,
                    source_import_id=result.id,
                    product_id=product_ids.get(parsed.external_product_id),
                )
                for parsed in parsed_rows
            ]
            inserted_count = self._repository.insert_sales_item_lines(lines=sales_lines)
            duplicate_count = len(sales_lines) - inserted_count
            daily_records = self._repository.rebuild_product_sales_daily(
                business_id=business_id,
                source_import_id=result.id,
                date_from=min(dates) if dates else period_start,
                date_to=max(dates) if dates else period_end,
            )

            result.status = SalesImportStatus.COMPLETED
            result.imported_count = inserted_count
            result.unique_ticket_count = len(tickets)
            result.unique_product_count = len(products)
            result.summary = {
                "csv_encoding": csv_encoding,
                "date_min": str(min(dates)) if dates else None,
                "date_max": str(max(dates)) if dates else None,
                "days_with_sales": len(dates),
                "parsed_line_count": len(sales_lines),
                "inserted_line_count": inserted_count,
                "duplicate_line_count": duplicate_count,
                "daily_record_count": len(daily_records),
                "unmatched_line_count": sum(
                    1 for parsed in parsed_rows if parsed.external_product_id not in product_ids
                ),
                "unmatched_product_count": len(
                    {
                        parsed.external_product_id
                        for parsed in parsed_rows
                        if parsed.external_product_id not in product_ids
                    }
                ),
                "zero_cost_line_count": sum(1 for parsed in parsed_rows if parsed.unit_cost == 0),
                "total_quantity": str(
                    sum((parsed.quantity for parsed in parsed_rows), Decimal("0"))
                ),
                "total_revenue": str(
                    sum((parsed.revenue for parsed in parsed_rows), Decimal("0"))
                ),
            }
            result.completed_at = datetime.now(UTC)
            return self._repository.update_sales_import(result)
        except Exception as exc:
            result.status = SalesImportStatus.FAILED
            result.errors.append(str(exc))
            result.completed_at = datetime.now(UTC)
            self._repository.update_sales_import(result)
            raise

    @staticmethod
    def _open_csv(csv_path: Path):
        last_error: UnicodeDecodeError | None = None
        for encoding in CSV_ENCODINGS:
            try:
                file = csv_path.open("r", encoding=encoding, newline="")
                file.read(4096)
                file.seek(0)
                return file
            except UnicodeDecodeError as exc:
                last_error = exc
        raise ValueError(
            f"Could not decode CSV with supported encodings: {CSV_ENCODINGS}"
        ) from last_error

    @staticmethod
    def _period_from_filename(filename: str) -> tuple[date, date]:
        match = SALES_FILENAME_PATTERN.search(filename)
        if match is None:
            raise ValueError(
                "Sales filename must include a period like ventasitems_YYYY-MM-DD_YYYY-MM-DD.csv"
            )
        return date.fromisoformat(match.group("start")), date.fromisoformat(match.group("end"))

    @staticmethod
    def _validate_headers(headers: list[str] | None) -> None:
        if headers is None:
            raise ValueError("Sales CSV has no headers.")
        missing = REQUIRED_COLUMNS.difference(headers)
        if missing:
            raise ValueError(f"Sales CSV is missing required columns: {sorted(missing)}")

    @staticmethod
    def _parse_row(row: dict[str, str]) -> "_ParsedSalesRow | None":
        sale_item_id = (row.get("id") or "").strip()
        external_product_id = (row.get("idproducto") or "").strip()
        ticket_id = (row.get("idventa") or "").strip()
        created_at = (row.get("fcreacion") or "").strip()
        quantity = parse_argentine_decimal(row.get("rcantidad"))
        revenue = parse_argentine_decimal(row.get("rtotal"))
        unit_cost = parse_argentine_decimal(row.get("rcostou")) or Decimal("0")
        if not sale_item_id or not external_product_id or not ticket_id or not created_at:
            return None
        if quantity is None or revenue is None:
            return None

        try:
            sold_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return None

        return _ParsedSalesRow(
            sale_item_id=sale_item_id,
            ticket_id=ticket_id,
            external_product_id=external_product_id,
            sku=(row.get("scodproducto") or "").strip() or None,
            sold_at=sold_at,
            quantity=quantity,
            revenue=revenue,
            unit_cost=unit_cost,
            raw_payload=dict(row),
        )


class _ParsedSalesRow:
    def __init__(
        self,
        *,
        sale_item_id: str,
        ticket_id: str,
        external_product_id: str,
        sku: str | None,
        sold_at: datetime,
        quantity: Decimal,
        revenue: Decimal,
        unit_cost: Decimal,
        raw_payload: dict[str, object],
    ) -> None:
        self.sale_item_id = sale_item_id
        self.ticket_id = ticket_id
        self.external_product_id = external_product_id
        self.sku = sku
        self.sold_at = sold_at
        self.sale_date = sold_at.date()
        self.quantity = quantity
        self.revenue = revenue
        self.unit_cost = unit_cost
        self.raw_payload = raw_payload

    def to_line(
        self,
        *,
        business_id: str,
        source_import_id: str,
        product_id: str | None,
    ) -> SalesItemLine:
        cost_amount = self.unit_cost * self.quantity
        return SalesItemLine(
            business_id=business_id,
            source_import_id=source_import_id,
            product_id=product_id,
            external_sale_item_id=self.sale_item_id,
            external_sale_id=self.ticket_id,
            external_product_id=self.external_product_id,
            sku=self.sku,
            sold_at=self.sold_at,
            sale_date=self.sale_date,
            quantity=self.quantity,
            revenue=self.revenue,
            unit_cost=self.unit_cost,
            cost_amount=cost_amount,
            gross_margin=self.revenue - cost_amount,
            raw_payload=self.raw_payload,
        )
