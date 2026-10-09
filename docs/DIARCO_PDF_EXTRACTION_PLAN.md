# Diarco PDF extraction plan

## Baseline

Source document: `C:\Users\54116\Downloads\diarco 0308 al 0708.pdf`

Observed on 2026-08-03:

- 20 pages.
- Each inspected page has 0 extractable text characters.
- Each inspected page is a single embedded JPG image.
- The current one-shot Gemini extraction persisted 56 items.
- Items by page from the persisted document:
  - page 1: 18
  - page 2: 20
  - page 3: 2
  - page 4: 3
  - page 5: 2
  - page 6: 1
  - page 7: 1
  - page 8: 2
  - page 9: 1
  - page 11: 2
  - page 13: 1
  - page 15: 1
  - page 16: 2

Diagnosis: the old flow sent the full visual PDF in a single model call. That produced a valid JSON response, but the extraction was not exhaustive after the first pages.

## Implementation direction

The extractor should treat image-only PDFs as page image batches:

1. Detect PDF pages with no embedded text and one image per page.
2. Extract each page image to a temporary file.
3. Send one page at a time to the document model.
4. Validate each page with `SupplierOfferPageExtraction`.
5. Aggregate items into the existing `SupplierOfferExtraction`.
6. Store extraction quality in document metadata.

The AI extracts candidates. The backend records coverage signals and flags suspicious pages for review.

## Coverage rules

The first deterministic rule is intentionally simple:

- If the model estimates visible offer blocks and extracted items are less than half, mark the page as `needs_review`.
- If a dense page estimates 8 or more blocks but extracts fewer than 5 items, mark it as `needs_review`.

Future improvement: split flagged pages into vertical/column tiles and retry only those pages.

## Current slice

Implemented:

- `pypdf` + `Pillow` dependencies for image extraction from PDF pages
- `SupplierOfferPageCoverage`
- `SupplierOfferPageExtraction`
- `SupplierOfferExtraction.page_extractions`
- `SupplierOfferExtraction.document_metadata`
- Gemini image-only PDF page extraction mode
- `supplier_offer_page_extractor.md`
- Provider tests for paginated aggregation

Not implemented yet:

- Automatic page tiling/retry.
- UI display for `needs_review_pages`.
- A full Diarco golden dataset with expected item counts.
