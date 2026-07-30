from decimal import Decimal

import pytest

from app.providers.documents.local_text import LocalTextSupplierOfferProvider


def test_local_text_supplier_offer_provider_extracts_simple_lines(tmp_path) -> None:
    document_path = tmp_path / "oferta-vital.txt"
    document_path.write_text(
        "CAÑUELAS ACEITE 900ML $2990\n"
        "ARROZ OKITA 1KG - $710\n"
        "LINEA SIN PRECIO\n",
        encoding="utf-8",
    )
    provider = LocalTextSupplierOfferProvider()

    extraction = provider.extract_supplier_offer(
        document_path=document_path,
        supplier_hint="Vital",
    )

    assert extraction.supplier_name == "Vital"
    assert extraction.source_filename == "oferta-vital.txt"
    assert [item.raw_name for item in extraction.items] == [
        "CAÑUELAS ACEITE 900ML",
        "ARROZ OKITA 1KG",
    ]
    assert extraction.items[0].offer_price == Decimal("2990")
    assert extraction.items[0].unit_size == Decimal("900")
    assert extraction.items[0].unit == "ml"
    assert extraction.items[1].offer_price == Decimal("710")
    assert extraction.items[1].unit_size == Decimal("1")
    assert extraction.items[1].unit == "kg"
    assert extraction.warnings == ["line 3: no price found"]


def test_local_text_supplier_offer_provider_rejects_pdf_files(tmp_path) -> None:
    document_path = tmp_path / "oferta.pdf"
    document_path.write_bytes(b"%PDF-1.7\nbinary")
    provider = LocalTextSupplierOfferProvider()

    with pytest.raises(ValueError, match="local_text only supports plain text"):
        provider.extract_supplier_offer(
            document_path=document_path,
            supplier_hint="Vital",
        )


def test_local_text_supplier_offer_provider_reads_cp1252_files(tmp_path) -> None:
    document_path = tmp_path / "oferta.txt"
    document_path.write_bytes("CAÑUELAS ACEITE 900ML $2990\n".encode("cp1252"))
    provider = LocalTextSupplierOfferProvider()

    extraction = provider.extract_supplier_offer(
        document_path=document_path,
        supplier_hint="Vital",
    )

    assert extraction.items[0].raw_name == "CAÑUELAS ACEITE 900ML"
