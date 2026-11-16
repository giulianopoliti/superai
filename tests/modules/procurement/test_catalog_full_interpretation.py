from app.modules.procurement.catalog_full_interpretation import (
    family_notice,
    full_options,
    guard_inferred_subtype,
    normalize_known_hierarchy,
    resolve_auxiliary_attributes,
    supported,
    validate_structure,
)
from app.providers.catalog_taxonomy_proposal import FullCatalogProposal, ProposedField


def proposal(**updates):
    fields = {
        key: {"value": None, "evidence": None}
        for key in ("category", "subcategory", "brand", "line", "variant", "packaging")
    }
    return FullCatalogProposal.model_validate(
        {**fields, "suggested_family": None, "family_reason": None, **updates}
    )


def test_zero_lactose_evidence_and_new_subcategory_are_preserved():
    source = {"original_name": "LECHE LA SERENISIMA 0 LACTOSA 1 LITRO"}
    p = proposal(
        category={"value": "Leche", "evidence": "LECHE"},
        subcategory={"value": "Leche sin lactosa", "evidence": "0 LACTOSA"},
        variant={"value": "Sin lactosa", "evidence": "0 LACTOSA"},
    )
    options = full_options(source, p, [], [], ["LACTEOS"])
    assert options["subcategory"][0].label == "Leche sin lactosa"
    assert options["variant"][0].evidence == "0 LACTOSA"


def test_single_word_identifiable_name_is_supported_but_invented_evidence_is_not():
    assert supported(
        {"original_name": "MANDARINA"}, ProposedField(value="Frutas", evidence="MANDARINA")
    )
    assert not supported(
        {"original_name": "ASD"}, ProposedField(value="Frutas", evidence="MANDARINA")
    )


def test_old_unrelated_fragments_do_not_compete_with_extracted_brand():
    source = {"original_name": "QUESO CREMOSO LA SERENISIMA"}
    p = proposal(brand={"value": "La Serenísima", "evidence": "LA SERENISIMA"})
    entries = [
        {
            "id": "old",
            "attribute": "brand",
            "label": "Cremoso",
            "aliases": [],
            "parent_id": None,
            "status": "proposed",
        }
    ]
    options = full_options(source, p, entries, [], ["LACTEOS"])
    assert [o.label for o in options["brand"]] == ["La Serenísima"]


def test_known_child_is_kept_under_its_existing_parent():
    p = proposal(category={"value": "Almidón de maíz", "evidence": "ALMIDON DE MAIZ"})
    entries = [
        {
            "id": "flour",
            "attribute": "category",
            "label": "Harinas y sémolas",
            "aliases": [],
            "parent_id": None,
            "status": "active",
        },
        {
            "id": "starch",
            "attribute": "subcategory",
            "label": "Almidón de maíz",
            "aliases": [],
            "parent_id": "flour",
            "status": "active",
        },
    ]
    normalized = normalize_known_hierarchy(p, entries)
    assert normalized.category.value == "Harinas y sémolas"
    assert normalized.subcategory.value == "Almidón de maíz"
    assert normalized.category.evidence == "ALMIDON DE MAIZ"


def test_a_brand_does_not_confirm_a_specific_subtype():
    p = proposal(
        brand={"value": "Jägermeister", "evidence": "JAGERMEISTER"},
        subcategory={"value": "Fernet", "evidence": "JAGERMEISTER"},
    )
    attrs = {
        "category": {"status": "interpreted", "value": "Bebidas alcohólicas"},
        "subcategory": {"status": "interpreted", "value": "Fernet"},
    }
    guard_inferred_subtype(attrs, p)
    assert attrs["subcategory"]["value"] is None
    assert attrs["product_type"]["value"] == "Bebidas alcohólicas"


def test_subcategory_cannot_follow_another_selected_parent():
    p = proposal(category={"value": "Café", "evidence": "CAFE"})
    attrs = {
        key: {"status": "unknown", "value": None}
        for key in ("category", "subcategory", "brand", "line", "variant")
    }
    attrs["category"] = {"status": "interpreted", "value": "Leche"}
    attrs["subcategory"] = {"status": "interpreted", "value": "Café en saquitos"}
    validate_structure(attrs, p)
    assert attrs["subcategory"]["status"] == "conflict"
    assert attrs["product_type"]["value"] == "Leche"


def test_family_change_is_only_a_user_review_notice():
    p = proposal(suggested_family="LACTEOS", family_reason="Es leche para personas")
    notice = family_notice(
        {"original_name": "LECHE", "category": "ALIMENTO PARA ANIMALES"},
        p,
        {"status": "interpreted", "value": "LACTEOS", "probability": 0.99},
    )
    assert notice["status"] == "change_suggested"
    assert notice["requires_user_review"] and not notice["original_family_modified"]


def test_uncertain_family_is_not_presented_as_confirmed_change():
    notice = family_notice(
        {"original_name": "ASD", "category": "ALIMENTO PARA ANIMALES"},
        proposal(),
        {"status": "low_probability", "value": None, "probability": 0.3},
    )
    assert notice["status"] == "needs_review" and notice["suggested_family"] is None


def test_supported_auxiliary_proposal_has_no_fabricated_probability():
    p = proposal(
        category={"value": "Textiles para el hogar", "evidence": "REPASADOR"},
        subcategory={"value": "Repasadores", "evidence": "REPASADOR"},
    )
    attrs = {key: {"value": None} for key in ("brand", "line", "variant")}
    resolve_auxiliary_attributes({"original_name": "REPASADOR"}, p, attrs)
    assert attrs["category"]["value"] == "Textiles para el hogar"
    assert attrs["subcategory"]["value"] == "Repasadores"
    assert attrs["category"]["probability"] is None
    assert attrs["category"]["origin"] == "structured_proposal"


def test_composition_remains_an_attribute_and_empty_reasons_are_distinct():
    p = proposal(
        category={"value": "Leche", "evidence": "LECHE"},
        subcategory={"value": "Leche sin lactosa", "evidence": "SIN LACTOSA"},
        variant={"value": "Sin lactosa", "evidence": "SIN LACTOSA"},
    )
    attrs = {key: {"value": None} for key in ("brand", "line", "variant")}
    resolve_auxiliary_attributes({"original_name": "LECHE SIN LACTOSA"}, p, attrs)
    assert attrs["subcategory"]["value"] is None
    assert attrs["subcategory"]["reason"] == "attribute_only_distinction"
    p = proposal(category_empty_reason="unidentifiable", subcategory_empty_reason="not_applicable")
    resolve_auxiliary_attributes({"original_name": "ASD"}, p, attrs)
    assert attrs["category"]["status"] == "unknown"
    assert attrs["subcategory"]["status"] == "not_applicable"


def test_invalid_auxiliary_evidence_is_not_accepted():
    attrs = {key: {"value": None} for key in ("brand", "line", "variant")}
    p = proposal(category={"value": "Frutas", "evidence": "MANDARINA"})
    resolve_auxiliary_attributes({"original_name": "ASD"}, p, attrs)
    assert attrs["category"]["status"] == "unsupported"
    assert attrs["category"]["value"] is None


def test_family_vote_cannot_turn_keep_into_a_move():
    p = proposal(suggested_family="FIAMBRERIA", family_action="keep", family_reason="Es razonable")
    notice = family_notice(
        {"original_name": "ACEITUNAS", "category": "FIAMBRERIA"},
        p,
        {"status": "interpreted", "value": "ALMACEN", "probability": 0.99},
    )
    assert notice["status"] == "needs_review"
    assert notice["suggested_family"] == "FIAMBRERIA"


def test_exceptional_new_family_is_only_a_review_proposal():
    p = proposal(
        family_action="propose_new",
        family_reason="Ninguna familia existente cubre estos productos",
        new_family={
            "name": "Mascotas",
            "description": "Productos para animales",
            "includes": ["Alimento para mascotas"],
            "excludes": ["Alimento humano"],
        },
    )
    notice = family_notice(
        {"original_name": "ALIMENTO PARA GATOS", "category": "BEBIDAS"},
        p,
        {"status": "none", "value": None, "probability": 0.99},
    )
    assert notice["status"] == "creation_suggested"
    assert notice["new_family"]["name"] == "Mascotas"
    assert notice["requires_user_review"] and not notice["original_family_modified"]


def test_repeated_category_is_not_a_meaningful_subcategory():
    attrs = {key: {"value": None} for key in ("brand", "line", "variant")}
    p = proposal(
        category={"value": "Papel higiénico", "evidence": "PAPEL HIGIENICO"},
        subcategory={"value": "Papel higiénico", "evidence": "PAPEL HIGIENICO"},
    )
    resolve_auxiliary_attributes({"original_name": "PAPEL HIGIENICO"}, p, attrs)
    assert attrs["subcategory"]["status"] == "not_applicable"
    assert attrs["subcategory"]["reason"] == "redundant_subcategory"
