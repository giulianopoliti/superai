from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app import main
from app.db.models import SupplierOfferDocumentModel
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.db.repositories.sql_reminders import SqlReminderRepository
from app.modules.procurement.analysis_contracts import AnalysisSummary
from app.modules.procurement.analysis_reporting import summary_from_metadata
from app.modules.procurement.schemas import Product, Supplier, SupplierOfferDocument
from app.modules.reminders.service import ReminderService
from app.settings import settings
from tests.test_operations_api import build_session


def test_home_is_compact_and_counts_all_reminders_and_documents(monkeypatch):
    factory = build_session()
    catalog = SqlProcurementRepository(factory)
    reminders = SqlReminderRepository(factory)
    for sku in ["one", "two"]:
        catalog.add_product(Product(
            business_id="one", external_product_id=sku, name=sku, normalized_name=sku,
            sale_price=Decimal("10"), current_cost=Decimal("2"),
        ))
    supplier = catalog.upsert_supplier(Supplier(
        business_id="one", name="Supplier", normalized_name="supplier",
    ))
    for number in range(7):
        catalog.add_supplier_offer_document(SupplierOfferDocument(
            business_id="one", supplier_id=supplier.id, source_filename=f"{number}.pdf",
            raw_text="private detail" * 1000, metadata={"reader_response": "heavy" * 20000},
        ))
        ReminderService(reminders).create_reminder(
            business_id="one", created_by_external_user_id="operator", title=f"Task {number}",
            due_at=datetime.now(UTC) + timedelta(days=1 if number == 6 else -1),
        )
    for key, value in {
        "llm_enabled": False, "agent_enabled": False, "scheduler_enabled": False,
        "web_auth_enabled": False, "jobs_enabled": False, "app_env": "test",
        "kapso_api_key": None,
    }.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(main, "SessionLocal", factory)
    statements = []

    @event.listens_for(factory.kw["bind"], "before_cursor_execute")
    def capture(_, __, sql, *args):
        statements.append(sql)

    with TestClient(main.create_app()) as client:
        csv = ("id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto\n"
               "one;ticket;one;1;10;2;2026-10-30T12:00:00Z;one\n"
               "two;ticket;two;1;10;2;2026-10-30T12:00:00Z;two\n")
        assert client.post("/sales/imports/from-file", data={"business_id": "one"}, files={
            "file": ("ventasitems_2026-10-30_2026-10-30.csv", csv.encode(), "text/csv"),
        }).status_code == 200
        statements.clear()
        response = client.get(
            "/operations/home?business_id=one&date_from=2026-10-24&date_to=2026-10-30"
        )
        assert response.status_code == 200, response.text
        assert len(statements) <= 6
        body = response.json()
        assert body["pending_reminder_count"] == 7
        assert body["overdue_reminder_count"] == 6
        assert body["supplier_offer_count"] == 7
        assert len(body["recent_supplier_offers"]) == 3
        assert body["sales_totals"]["ticket_count"] == 1
        assert Decimal(body["sales_totals"]["revenue"]) == 20
        assert body["sales_status"]["total_sale_lines"] == 2
        assert len(response.content) < 20000
        assert "reader_response" not in response.text and "private detail" not in response.text
        assert all("raw_text" not in sql and "metadata" not in sql for sql in statements)
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["server-timing"].startswith("app;dur=")
        first = catalog.search_products(business_id="one", query="one")[0]
        assert client.post(f"/procurement/products/{first.id}/analysis-preference", json={
            "business_id": "one", "exclude_from_sales_rankings": True,
        }).status_code == 200
        filtered = client.get(
            "/operations/home?business_id=one&date_from=2026-10-24&date_to=2026-10-30"
        ).json()
        assert [item["external_product_id"] for item in filtered["top_products_by_quantity"]] == [
            "two"
        ]
        assert Decimal(filtered["sales_totals"]["revenue"]) == 20
        other = client.get(
            "/operations/home?business_id=two&date_from=2026-10-24&date_to=2026-10-30"
        ).json()
        assert other["supplier_offer_count"] == other["pending_reminder_count"] == 0
        assert other["recent_supplier_offers"] == other["top_products_by_quantity"] == []
        assert client.get(
            "/operations/home?business_id=one&date_from=2026-10-30&date_to=2026-10-24"
        ).status_code == 422
    factory.kw["bind"].dispose()


@pytest.mark.parametrize("ledger", [
    None,
    {"run_id": "different", "pages": [], "calls": []},
    {"run_id": "run", "pages": [{"page_number": 1, "initial_done": True}]},
    {"run_id": "run", "pages": [None, "historical", {"page_number": 1, "initial_done": True}],
     "calls": [None, "historical", {"phase": "initial"}], "decision_audit": [{}, None]},
])
def test_summary_projection_preserves_historical_unknown_evidence(ledger):
    factory = build_session()
    repository = SqlProcurementRepository(factory)
    supplier = repository.upsert_supplier(Supplier(
        business_id="one", name="Supplier", normalized_name="supplier",
    ))
    metadata = {"analysis": AnalysisSummary(
        run_id="run", method="openai_structured", status="partial", page_count=3,
        observed_block_count=0, interpreted_offer_count=0, pending_block_count=0,
    ).model_dump(mode="json")}
    if ledger is not None:
        metadata["analysis_ledger"] = ledger
    repository.add_supplier_offer_document(SupplierOfferDocument(
        business_id="one", supplier_id=supplier.id, source_filename="historical.pdf",
        metadata=metadata,
    ))
    projected = repository.list_supplier_offer_document_summaries(business_id="one")[0].document
    assert summary_from_metadata(projected.metadata) == summary_from_metadata(metadata)
    factory.kw["bind"].dispose()


def test_document_list_projects_summary_evidence_and_keeps_full_detail():
    factory = build_session()
    repository = SqlProcurementRepository(factory)
    supplier = repository.upsert_supplier(Supplier(
        business_id="one", name="Supplier", normalized_name="supplier",
    ))
    analysis = AnalysisSummary(
        run_id="run", method="openai_structured", status="partial", page_count=3,
        observed_block_count=1, interpreted_offer_count=1, pending_block_count=1,
    ).model_dump(mode="json")
    metadata = {"analysis": analysis, "private_payload": "large" * 20000,
                "analysis_ledger": {"run_id": "run", "pages": [
                    {"page_number": 1, "initial_done": True, "reader_output": "large" * 20000},
                ], "calls": [
                    {"phase": "initial", "start_page": 1, "end_page": 1, "body": "large" * 20000},
                    {"phase": "initial", "start_page": 2, "end_page": 2},
                ], "decision_audit": [
                    {"reason_codes": ["jev_request_limit"], "call_ids": [],
                     "debug": "large" * 20000},
                ]}}
    doc = repository.add_supplier_offer_document(SupplierOfferDocument(
        business_id="one", supplier_id=supplier.id, source_filename="offers.pdf",
        raw_text="original private text", metadata=metadata,
    ))
    entries = repository.list_supplier_offer_document_summaries(business_id="one")
    assert len(entries) == 1
    projected = entries[0].document
    assert len(projected.model_dump_json()) < 12000
    assert projected.raw_text is None and "private_payload" not in projected.metadata
    assert summary_from_metadata(projected.metadata) == summary_from_metadata(metadata)
    assert summary_from_metadata(projected.metadata).failed_page_numbers == [2]
    assert summary_from_metadata(projected.metadata).unattempted_page_numbers == [3]
    assert summary_from_metadata(projected.metadata).local_rejection_count == 1
    with factory() as session:
        assert session.scalar(select(SupplierOfferDocumentModel.metadata_).where(
            SupplierOfferDocumentModel.id == doc.id,
        )) == metadata
    detail = repository.get_supplier_offer_document(
        business_id="one", supplier_offer_document_id=doc.id,
    )
    assert detail.metadata == metadata and detail.raw_text == "original private text"
    assert repository.list_supplier_offer_document_summaries(business_id="other") == []
    factory.kw["bind"].dispose()
