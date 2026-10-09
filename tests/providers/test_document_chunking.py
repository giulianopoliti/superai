import pytest
from pypdf import PdfReader, PdfWriter

from app.providers.documents.base import DocumentPageContext
from app.providers.documents.chunking import count_pdf_pages, pdf_chunks


def shared_image_pdf(tmp_path, *, form=False, annotations=False):
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
    )

    writer = PdfWriter()
    images = DictionaryObject()
    for index in range(3):
        image = DecodedStreamObject()
        image.set_data(bytes([index + 1]) * 30_000)
        image.update({
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(100),
            NameObject("/Height"): NumberObject(100),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        })
        images[NameObject(f"/I{index}")] = writer._add_object(image)
    if form:
        stream = DecodedStreamObject()
        stream.set_data(b"/I0 Do")
        stream.update({
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject([NumberObject(n) for n in (0, 0, 100, 100)]),
        })
        images[NameObject("/F0")] = writer._add_object(stream)
    resources = writer._add_object(DictionaryObject({NameObject("/XObject"): images}))
    for index in range(3):
        page = writer.add_blank_page(width=100, height=100)
        page[NameObject("/Resources")] = resources
        contents = DecodedStreamObject()
        contents.set_data(b"/F0 Do" if form else f"/I{index} Do".encode())
        page[NameObject("/Contents")] = writer._add_object(contents)
        if annotations:
            page[NameObject("/Annots")] = ArrayObject([DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Text"),
                NameObject("/Rect"): ArrayObject([NumberObject(n) for n in (0, 0, 10, 10)]),
            })])
    path = tmp_path / "shared-images.pdf"
    writer.write(path)
    return path


@pytest.mark.parametrize("chunk_size", [1, 2])
def test_chunks_keep_only_used_page_images_and_preserve_original_content(tmp_path, chunk_size):
    path = shared_image_pdf(tmp_path)
    original = PdfReader(path)
    assert all(len(p["/Resources"]["/XObject"]) == 3 for p in original.pages)
    with pdf_chunks(path, chunk_size=chunk_size) as chunks:
        for chunk in chunks:
            assert chunk.path.stat().st_size < path.stat().st_size * 0.8
            for local, page in enumerate(PdfReader(chunk.path).pages):
                index = chunk.start_page - 1 + local
                images = page["/Resources"]["/XObject"]
                assert list(images) == [f"/I{index}"]
                assert images[f"/I{index}"].get_data() == bytes([index + 1]) * 30_000
                assert (
                    page.get_contents().get_data()
                    == original.pages[index].get_contents().get_data()
                )
    assert all(len(p["/Resources"]["/XObject"]) == 3 for p in PdfReader(path).pages)


@pytest.mark.parametrize("kind", ["form", "annotations"])
def test_complex_appearances_keep_inherited_resources(tmp_path, kind):
    path = shared_image_pdf(tmp_path, **{kind: True})
    with pdf_chunks(path, chunk_size=1) as chunks:
        page = PdfReader(chunks[0].path).pages[0]
        assert len(page["/Resources"]["/XObject"]) == (4 if kind == "form" else 3)


def test_chunk_ranges_content_order_and_cleanup(tmp_path):
    path = tmp_path / "source.pdf"
    writer = PdfWriter()
    for page in range(1, 28):
        writer.add_blank_page(width=page * 10, height=100)
    writer.write(path)
    assert count_pdf_pages(path) == 27
    with pdf_chunks(path) as chunks:
        assert [c.path.name for c in chunks] == [
            "chunk-0001-0006.pdf",
            "chunk-0007-0012.pdf",
            "chunk-0013-0018.pdf",
            "chunk-0019-0024.pdf",
            "chunk-0025-0027.pdf",
        ]
        assert [c.page_count for c in chunks] == [6, 6, 6, 6, 3]
        assert [float(page.mediabox.width) for c in chunks for page in PdfReader(c.path).pages] == [
            page * 10 for page in range(1, 28)
        ]
    assert all(not c.path.exists() for c in chunks)
    with pytest.raises(RuntimeError), pdf_chunks(path, pages=[14, 1, 14]) as pages:
        assert [(c.start_page, c.end_page) for c in pages] == [(1, 1), (14, 14)]
        raise RuntimeError("test failure")
    assert all(not c.path.exists() for c in pages)


def test_page_context_rejects_out_of_range_numbers():
    context = DocumentPageContext(source_filename="source.pdf", start_page=25, end_page=27)
    assert context.original_page(1) == 25
    assert context.original_page(3) == 27
    assert all(context.original_page(page) is None for page in (None, 0, -1, 4, 27))
    with pytest.raises(ValueError):
        DocumentPageContext(source_filename="x", start_page=6, end_page=1)


def test_conflicting_nested_page_numbers_are_preserved_but_not_assigned():
    from app.modules.procurement.schemas import SupplierOfferReaderExtraction
    from app.providers.documents.base import reader_extraction
    from app.providers.documents.dual import DualDocumentExtractionProvider

    raw = SupplierOfferReaderExtraction(
        page_extractions=[
            {
                "page_number": 2,
                "items": [{"raw_name": "Arroz", "offer_price": 100, "page_number": 1}],
            }
        ]
    )
    extracted = reader_extraction(raw, source_filename="source.pdf", provider="gemini")
    context = DocumentPageContext(source_filename="source.pdf", start_page=7, end_page=12)
    mapped = DualDocumentExtractionProvider._map_pages(extracted, context, "gemini")
    assert mapped.items[0].page_number is None
    assert mapped.items[0].resolved_by == "human_review"
    assert mapped.items[0].provider_observations["gemini"]["page_number"] == 1
    assert mapped.document_metadata["invalid_page_numbers"] is True


def test_chunk_configuration_defaults_and_limits(monkeypatch):
    from app.settings import Settings

    names = [
        "DOCUMENT_EXTRACTION_CHUNK_SIZE",
        "DOCUMENT_EXTRACTION_CHUNK_WORKERS",
        "DOCUMENT_EXTRACTION_RETRY_WORKERS",
        "DOCUMENT_EXTRACTION_TIMEOUT_SECONDS",
        "DOCUMENT_EXTRACTION_RETRY_LOW_COVERAGE",
    ]
    for name in names:
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.document_extraction_chunk_size == 2
    assert settings.document_extraction_chunk_workers == 2
    assert settings.document_extraction_retry_workers == 2
    assert settings.document_extraction_timeout_seconds == 60
    assert settings.document_extraction_retry_low_coverage is True
    for name, value in ((names[0], 7), (names[1], 3), (names[2], 3), (names[3], 0)):
        with pytest.raises(ValueError):
            Settings(_env_file=None, **{name: value})
