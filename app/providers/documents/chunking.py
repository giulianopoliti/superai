"""Temporary ordered PDF chunks, including sparse single-page rereads."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject


def _page_with_used_images(page):
    """Detach shared image resources without altering the source page.

    Scanned brochures can share all page images in one resource dictionary.
    Keep only images invoked by this page's content. Leave forms/annotations
    untouched: their appearance streams may rely on inherited resources.
    """
    if page.get("/Annots") or "/Resources" not in page:
        return page
    resources = page["/Resources"]
    if "/XObject" not in resources:
        return page
    xobjects = resources["/XObject"]
    contents = page.get_contents()
    if contents is None:
        return page
    used = {operands[0] for operands, op in contents.operations if op == b"Do"}
    if any(
        name not in xobjects or xobjects[name].get("/Subtype") != "/Image" for name in used
    ):
        return page
    detached = copy(page)
    detached_resources = DictionaryObject(resources)
    detached_resources[NameObject("/XObject")] = DictionaryObject(
        {name: obj for name, obj in xobjects.items() if name in used}
    )
    detached[NameObject("/Resources")] = detached_resources
    return detached


@dataclass(frozen=True)
class PdfChunk:
    path: Path
    index: int
    start_page: int
    end_page: int
    page_count: int


def count_pdf_pages(path: Path) -> int:
    with path.open("rb") as source:
        return len(PdfReader(source).pages)


@contextmanager
def pdf_chunks(
    path: Path,
    chunk_size: int = 6,
    *,
    pages: Sequence[int] | None = None,
) -> Iterator[list[PdfChunk]]:
    if not 1 <= chunk_size <= 6:
        raise ValueError("chunk_size must be between 1 and 6")
    with path.open("rb") as source, TemporaryDirectory(prefix="stock-ai-chunks-") as directory:
        reader = PdfReader(source)
        count = len(reader.pages)
        if not count:
            raise ValueError("PDF contains no pages")
        if pages is None:
            ranges = [
                (start, min(start + chunk_size - 1, count))
                for start in range(1, count + 1, chunk_size)
            ]
        else:
            if any(page < 1 or page > count for page in pages):
                raise ValueError("Requested page outside PDF")
            ranges = [(page, page) for page in sorted(set(pages))]
        chunks = []
        for index, (start, end) in enumerate(ranges):
            chunk_path = Path(directory) / f"chunk-{start:04d}-{end:04d}.pdf"
            writer = PdfWriter()
            for page in range(start - 1, end):
                writer.add_page(_page_with_used_images(reader.pages[page]))
            with chunk_path.open("wb") as output:
                writer.write(output)
            writer.close()
            chunks.append(PdfChunk(chunk_path, index, start, end, end - start + 1))
        yield chunks
