"""No providers: real repositories, auxiliary records and deterministic matching."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    CatalogInterpretationModel,
    CatalogInterpretationNeedModel,
    CatalogVocabularyModel,
    ProductModel,
    ProductSalesDailyModel,
    SalesItemLineModel,
)
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.db.repositories.sql_sales import SqlSalesRepository
from app.modules.catalog.product_view import PROMPT_HEADER, AuxiliaryProduct
from app.modules.catalog.resolver import CatalogResolverService
from app.modules.procurement.catalog_interpretation import digest, snapshot
from app.modules.procurement.identity import auxiliary_identity_gaps, identity_conflicts
from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.schemas import Product, ProductMatchFeedbackRule, SupplierOfferItem
from app.modules.sales.sales_import import PosSalesImportService


@pytest.fixture
def repositories():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory, SqlProcurementRepository(factory), SqlSalesRepository(factory)


def state(value, status="interpreted"):
    return {"value": value, "status": status, "evidence": "source name", "origin": "fixture"}


def auxiliary(**attributes):
    return AuxiliaryProduct(
        interpretation_id="fixture", source_hash="hash", config_hash="config",
        attributes={k: state(v) for k, v in attributes.items()},
    )


def product(name="NQ CAC 360G", **updates):
    return Product(**{
        "business_id": "a", "external_product_id": "pos-1", "name": name,
        "normalized_name": name.lower(), **updates,
    })


def offer(name="Nesquik cacao 360 g", **updates):
    return SupplierOfferItem(
        business_id="a", supplier_offer_document_id="doc", supplier_id="supplier",
        raw_name=name, normalized_name=name.lower(), offer_price=Decimal("100"), **updates,
    )


def save_aux(factory, p, *, attrs=None, header=PROMPT_HEADER, source_hash=None, age=0):
    with factory() as session:
        model = session.get(ProductModel, p.id)
        row = CatalogInterpretationModel(
            id=f"{p.id}-{age}", run_id="fixture", business_id=p.business_id, product_id=p.id,
            source_hash=source_hash or digest(snapshot(model)), config_hash=f"config-{age}",
            source=snapshot(model), standard_name="Untrusted full proposed name",
            status="needs_review", attributes=attrs or {
                "brand": state("Nesquik"), "product_type": state("Cacao"),
                "category": state("Desayuno"), "subcategory": state("Cacao"),
                "content": state({"value": "360", "unit": "g"}),
            }, request={"prompt": header + "\n\nfixture"}, response={},
            created_at=datetime.now(UTC) + timedelta(seconds=age),
        )
        session.add(row)
        session.commit()


def test_decisions_first_and_previous_interpretations_coexist(repositories):
    factory, repo, _ = repositories
    previous = repo.add_product(product(external_product_id="previous"))
    added = repo.add_product(product("CACAO MARCA NUEVA 180G", external_product_id="added"))
    save_aux(factory, previous)
    save_aux(factory, added, header="# catalog_decisions_first v1")
    found = {p.id: p for p in repo.list_products(business_id="a")}
    assert found[previous.id].auxiliary is not None
    assert found[added.id].auxiliary is not None


def test_batch_read_selects_fresh_admitted_revision_and_preserves_original(repositories):
    factory, repo, _ = repositories
    p = repo.add_product(product(category="Almacen original"))
    other = repo.add_product(product("Otro", external_product_id=None)
                             .model_copy(update={"business_id": "b"}))
    save_aux(factory, p)
    save_aux(factory, p, source_hash="stale", age=10)
    save_aux(factory, p, header="# catalog_full_interpretation v4", age=20)
    save_aux(factory, other)
    queries = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)
    event.listen(factory.kw["bind"], "before_cursor_execute", capture)
    [loaded] = repo.list_products(business_id="a")
    assert loaded.id == p.id and loaded.name == p.name
    assert loaded.category == "Almacen original"
    assert loaded.auxiliary.interpretation_id == f"{p.id}-0"
    assert loaded.auxiliary.value("category") == "Desayuno"
    assert len(queries) == 3  # products + interpretations + vocabulary, regardless of catalog size
    assert repo.get_product(business_id="a", product_id=other.id) is None


def test_matching_before_after_and_fuzzy_retrieval(repositories):
    factory, repo, _ = repositories
    p = repo.add_product(product())
    save_aux(factory, p)
    service = ProductMatchService(None)
    old = service.match_item(item=offer(), products=[p])[0]
    loaded = repo.list_products(business_id="a")
    new = service.match_item(item=offer(), products=loaded)[0]
    assert not old.auto_accepted
    assert new.auto_accepted and new.product.id == p.id
    assert new.confidence_score > old.confidence_score
    typo = service.match_item(item=offer("Nesquik caco 360 g"), products=loaded)[0]
    assert typo.product.id == p.id and not typo.auto_accepted
    assert CatalogResolverService(repo).resolve_product(
        business_id="a", query="Nesquik cacao 360 g"
    ).product.id == p.id
    assert repo.search_products(business_id="a", query="Cacao Nesquik")[0].id == p.id


@pytest.mark.parametrize("header", [PROMPT_HEADER, "# catalog_decisions_first v1"])
def test_comparator_uses_original_fields_without_reading_or_removing_classifications(
    repositories, monkeypatch, header,
):
    from app import main

    factory, enriched_repo, _ = repositories
    p = enriched_repo.add_product(product(
        "Cacao Nesquik 360 g", brand="Nesquik", unit_size=Decimal(360), unit="g",
        current_cost=Decimal(200),
    ))
    save_aux(factory, p, header=header, attrs={
        "brand": state("Arlistan"), "product_type": state("Cafe"),
        "content": state({"value": "500", "unit": "g"}),
    })
    enriched = enriched_repo.get_product(business_id="a", product_id=p.id)
    item = offer(brand="Nesquik", unit_size=Decimal(360), unit="g")
    assert not ProductMatchService(None).match_item(item=item, products=[enriched])[0].auto_accepted

    monkeypatch.setattr(main, "SessionLocal", factory)
    repo = main.build_procurement_repository()
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        [loaded] = repo.list_products(business_id="a")
        reads = [
            loaded,
            repo.get_product(business_id="a", product_id=p.id),
            repo.list_products_by_ids(business_id="a", product_ids={p.id})[0],
            repo.search_products(business_id="a", query="Nesquik")[0],
        ]
        assert all(row.auxiliary is None and snapshot(row) == snapshot(p) for row in reads)
        assert repo.search_products(business_id="a", query="Arlistan") == []
        assert repo.get_product(business_id="b", product_id=p.id) is None
        assert not any("catalog_interpretations" in sql or "catalog_vocabulary" in sql
                       for sql in statements)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    match = ProductMatchService(None).match_item(item=item, products=[loaded])[0]
    assert match.auto_accepted and match.product.id == p.id
    assert match.cost_difference == Decimal(100)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(CatalogInterpretationModel)) == 1
        assert session.get(ProductModel, p.id).name == p.name


def test_mixed_catalog_matches_enriched_original_and_stale_without_excluding_any(repositories):
    factory, repo, _ = repositories
    enriched = repo.add_product(product())
    original = repo.add_product(product("Arlistan cafe 170 g", external_product_id="pos-2"))
    stale = repo.add_product(product("Cabrales cafe 250 g", external_product_id="pos-3"))
    save_aux(factory, enriched)
    save_aux(factory, stale, source_hash="obsolete")
    products = repo.list_products(business_id="a")
    assert len(products) == 3
    assert sum(p.auxiliary is not None for p in products) == 1
    service = ProductMatchService(None)
    for query, brand, expected in (
        ("Nesquik cacao 360 g", "Nesquik", enriched),
        ("Arlistan cafe 170 g", "Arlistan", original),
        ("Cabrales cafe 250 g", "Cabrales", stale),
    ):
        [candidate] = service.match_item(item=offer(query, brand=brand), products=products)
        assert candidate.product.id == expected.id and candidate.auto_accepted


def test_mixed_catalog_does_not_prefer_interpreted_product_over_duplicate_identity(repositories):
    factory, repo, _ = repositories
    enriched = repo.add_product(product())
    repo.add_product(product("Nesquik cacao 360 g", external_product_id="pos-2"))
    save_aux(factory, enriched)
    candidates = ProductMatchService(None).match_item(
        item=offer(), products=repo.list_products(business_id="a"), limit=2,
    )
    assert len(candidates) == 2 and all(not c.auto_accepted for c in candidates)


def test_brand_conflict_survives_high_text_score_and_feedback():
    p = product("Nescafe Dolca 170 g", auxiliary=auxiliary(brand="Arlistan"))
    item = offer("Nescafe Dolca 170 g", brand="Nescafe")
    service = ProductMatchService(None)
    candidate = service.match_item(item=item, products=[p])[0]
    assert not candidate.auto_accepted
    assert "brand_conflict" in candidate.reasons
    rule = ProductMatchFeedbackRule(
        supplier_id=item.supplier_id, supplier_offer_item_normalized_name=item.normalized_name,
        candidate_product_id=p.id, accepted=True, relationship_type="exact_match",
        created_at=datetime.now(UTC),
    )
    reviewed = service._match_item_with_feedback(
        item=item, products=[p], feedback_rules=[rule], limit=1,
    )[0]
    assert reviewed.product.id == p.id and "confirmed_by_feedback" in reviewed.reasons
    assert not reviewed.auto_accepted


@pytest.mark.parametrize("interpreted", [False, True])
@pytest.mark.parametrize("name", ["Nescafe Dolca 170 g", "Nescafe Dolca Origen 170 g"])
def test_unspecified_dolca_variant_never_forces_one(interpreted, name):
    products = [product(
        f"Nescafe Dolca {variant} 170 g", auxiliary=auxiliary(
            brand="Nescafe", line="Dolca", variant=variant,
        ) if interpreted else None,
    ) for variant in ("Origen", "Origen Suave")]
    candidates = ProductMatchService(None).match_item(
        item=offer(name, brand="Nescafe"), products=products, limit=2,
    )
    assert all(not c.auto_accepted for c in candidates)
    explicit = ProductMatchService(None).match_item(
        item=offer("Nescafe Dolca Origen Suave 170 g", brand="Nescafe"), products=products,
    )[0]
    assert explicit.auto_accepted and explicit.product.id == products[1].id


def test_partial_attributes_aliases_and_quantities_remain_distinct():
    view = auxiliary(brand="Nesquik", product_type="Cacao", content={"value": "360", "unit": "g"})
    view.attributes["variant"] = state("Suave", "needs_review")
    view.aliases["brand"] = ["NQ"]
    assert view.value("variant") is None
    p = product(auxiliary=view)
    assert not identity_conflicts(offer(package_quantity=12, metadata={"minimum_purchase": 24}), p)
    assert "presentation_conflict" in identity_conflicts(offer(unit_size=Decimal(720), unit="g"), p)
    assert "brand_conflict" not in identity_conflicts(offer(brand="NQ"), p)
    assert "brand_conflict" not in identity_conflicts(offer(), product())
    view.attributes["package_quantity"] = state(6)
    assert "retail_count_conflict" in identity_conflicts(offer(metadata={
        "analysis_v2": {"interpretation": {"retail_contents_count": 12}}
    }), p)


def test_persisted_v2_identity_fields_keep_discriminators_and_retail_count():
    p = product(auxiliary=auxiliary(variant="Suave", package_quantity=6))
    item = offer(metadata={"analysis_v2": {"interpretation": {
        "identity_attributes": {"variant": "Suave"}, "retail_contents_count": 6,
    }}})
    assert not auxiliary_identity_gaps(item, p)
    assert not identity_conflicts(item, p)
    item.metadata["analysis_v2"]["interpretation"]["identity_attributes"]["variant"] = "Fuerte"
    assert "variant_conflict" in identity_conflicts(item, p)


def test_auxiliary_variant_ambiguity_uses_content_when_original_names_are_abbreviated():
    fields = dict(brand="Nesquik", product_type="Cacao", content={"value": "360", "unit": "g"})
    base = product("NQ CAC", auxiliary=auxiliary(**fields))
    variant = product("NQ CAC SVE", auxiliary=auxiliary(**fields, variant="Suave"))
    [candidate] = ProductMatchService(None).match_item(item=offer(), products=[base, variant])
    assert not candidate.auto_accepted and "catalog_variant_ambiguity" in candidate.reasons


def test_repeated_import_price_stock_reuse_and_relevant_change(repositories):
    factory, repo, _ = repositories
    p = product(unit_size=Decimal("360"), unit="g")
    repo.upsert_products([p])
    persisted = repo.list_products(business_id="a")[0]
    save_aux(factory, persisted)
    priced = p.model_copy(update={"current_cost": Decimal(100), "stock_quantity": Decimal(9)})
    repo.upsert_products([priced])
    assert repo.list_products(business_id="a")[0].auxiliary is not None
    with factory() as s:
        need = s.get(CatalogInterpretationNeedModel, ("a", persisted.id))
        assert need.status == "current"
        timestamp = need.updated_at
    repo.upsert_products([priced])
    with factory() as s:
        assert s.get(CatalogInterpretationNeedModel, ("a", persisted.id)).updated_at == timestamp
    changed = priced.model_copy(update={"name": "NQ CAC 500G"})
    repo.upsert_products([changed])
    repo.upsert_products([changed])
    assert repo.list_products(business_id="a")[0].auxiliary is None
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(CatalogInterpretationNeedModel)) == 1
        assert s.get(CatalogInterpretationNeedModel, ("a", persisted.id)).status == "pending"


@pytest.mark.parametrize("field,value", [
    ("brand", "Otra marca"), ("category", "Otra familia"),
    ("unit_size", Decimal("500")), ("external_product_id", "new-pos-id"),
])
def test_source_changes_require_new_interpretation(repositories, field, value):
    factory, repo, _ = repositories
    p = repo.add_product(product(unit_size=Decimal(360), unit="g"))
    save_aux(factory, p)
    # Preserve the product PK; external ID changes are modeled as source edits here.
    with factory() as s:
        setattr(s.get(ProductModel, p.id), field, value)
        s.commit()
    assert repo.get_product(business_id="a", product_id=p.id).auxiliary is None


def test_taxonomy_without_brand_does_not_establish_identity():
    p = product(auxiliary=auxiliary(
        product_type="Cacao", category="Desayuno", content={"value": "360", "unit": "g"},
    ))
    [candidate] = ProductMatchService(None).match_item(item=offer("Cacao 360 g"), products=[p])
    assert not candidate.auto_accepted


def test_products_without_interpretation_remain_searchable(repositories):
    _, repo, _ = repositories
    p = repo.add_product(product("Arlistan cafe 170 g"))
    assert repo.list_products(business_id="a")[0].auxiliary is None
    assert repo.search_products(business_id="a", query="Arlistan")[0].id == p.id
    assert CatalogResolverService(repo).resolve_product(
        business_id="a", query="Arlistan cafe 170 g"
    ).product.id == p.id


@pytest.mark.parametrize("business,status,expected", [
    ("a", "active", True), ("a", "proposed", False), ("b", "active", False),
])
def test_only_active_tenant_vocabulary_aliases_are_used(repositories, business, status, expected):
    factory, repo, _ = repositories
    p = repo.add_product(product())
    attrs = {
        "brand": {**state("Nesquik"), "vocabulary_id": "brand"},
        "content": state({"value": "360", "unit": "g"}),
    }
    save_aux(factory, p, attrs=attrs)
    with factory() as s:
        s.add(CatalogVocabularyModel(
            id="brand", business_id=business, attribute="brand", label="Nesquik",
            normalized_label="nesquik", aliases=["NQ"], status=status,
        ))
        s.commit()
    view = repo.list_products(business_id="a")[0].auxiliary
    assert ("nq" in view.terms("brand")) == expected
    assert view.attributes["brand"]["origin"] == "fixture"


def test_sales_before_catalog_exact_reconciliation_and_auxiliary_filters(repositories, tmp_path):
    factory, repo, sales = repositories
    path = tmp_path / "ventasitems_2026-06-30_2026-07-30.csv"
    path.write_text(
        "id;idventa;idproducto;rcantidad;rtotal;rcostou;fcreacion;scodproducto\n"
        "l1;t1;pos-1;2;200;50;2026-06-30T10:00:00Z;sku\n", encoding="utf-8",
    )
    service = PosSalesImportService(sales)
    service.import_csv(business_id="a", csv_path=path)
    service.import_csv(business_id="b", csv_path=path)
    p = repo.add_product(product(category="Familia original"))
    save_aux(factory, p)
    repo.upsert_products([p])
    service.import_csv(business_id="a", csv_path=path)
    dates = dict(business_id="a", date_from=date(2026, 6, 30), date_to=date(2026, 7, 30))
    totals = sales.query_sales_summary(**dates, auxiliary_filters={"category": "Desayuno"})
    assert totals["quantity_sold"] == 2 and totals["ticket_count"] == 1
    assert sales.query_sales_summary(**dates, auxiliary_filters={"content": "0.36 kg"})[
        "quantity_sold"] == 2
    assert sales.query_sales_summary(**dates, categories=["Desayuno"])["quantity_sold"] == 0
    assert sales.query_sales_summary(**dates, categories=["Familia original"])["quantity_sold"] == 2
    assert sales.query_sales_summary(**dates, auxiliary_filters={"brand": "Otro"})[
        "quantity_sold"] == 0
    [top] = sales.top_products(**dates, sort="quantity", limit=10,
                              auxiliary_filters={"subcategory": "Cacao", "brand": "Nesquik"})
    assert top.product_id == p.id and top.quantity_sold == 2
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(SalesItemLineModel)) == 2
        assert s.scalar(select(func.count()).select_from(ProductSalesDailyModel)) == 2
        for model in (SalesItemLineModel, ProductSalesDailyModel):
            assert s.scalar(select(model.product_id).where(model.business_id == "a")) == p.id
            assert s.scalar(select(model.product_id).where(model.business_id == "b")) is None
