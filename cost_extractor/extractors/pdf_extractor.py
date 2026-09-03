"""Extracts text segments from a PDF: text layer first, OCR fallback per page."""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pdfplumber
import pdfplumber.utils.exceptions
import pypdfium2 as pdfium
from pdfminer.pdfdocument import PDFEncryptionError

from cost_extractor import ocr_reader
from cost_extractor.extractors.base import (
    ExtractionResult,
    PositionedToken,
    Status,
    TextSegment,
)

_OCR_RENDER_SCALE = 300 / 72  # ~300 DPI


def _is_password_error(e: pdfplumber.utils.exceptions.PdfminerException) -> bool:
    cause = e.args[0] if e.args else None
    return isinstance(cause, PDFEncryptionError)


def _render_page(page_index: int, path: Path):
    """Renders one page at the exact scale OCR reads it at."""
    pdf = pdfium.PdfDocument(str(path))
    try:
        return pdf[page_index].render(scale=_OCR_RENDER_SCALE).to_pil()
    finally:
        pdf.close()


def _ocr_page(page_index: int, path: Path) -> tuple[str, list[PositionedToken]]:
    return ocr_reader.read_image(_render_page(page_index, path))


def extract(path: Path, ocr_enabled: bool = True) -> ExtractionResult:
    try:
        pdf = pdfplumber.open(path)
    except pdfplumber.utils.exceptions.PdfminerException as e:
        if _is_password_error(e):
            return ExtractionResult(
                status=Status.ERROR, error_message="password-protected PDF"
            )
        return ExtractionResult(
            status=Status.ERROR, error_message=f"corrupt or unreadable PDF: {e}"
        )
    except Exception as e:  # noqa: BLE001 - any other open failure is a hard error
        return ExtractionResult(
            status=Status.ERROR, error_message=f"corrupt or unreadable PDF: {e}"
        )

    segments: list[TextSegment] = []
    warnings: list[str] = []

    with pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                segments.append(TextSegment(text=text, location=f"page {i}"))
                continue

            if not ocr_enabled:
                continue

            try:
                # Deliberately not stripped: token offsets index into this
                # exact string, and trimming it would slide every box one
                # word to the left. The reader only inserts separators
                # between words, so there is no leading/trailing space to
                # trim anyway.
                ocr_text, tokens = _ocr_page(i - 1, path)
            except Exception as e:  # noqa: BLE001 - one bad page must not fail the doc
                warnings.append(f"OCR failed on page {i}: {e}")
                continue

            if ocr_text:
                segments.append(
                    TextSegment(
                        text=ocr_text,
                        location=f"page {i}",
                        provenance="ocr",
                        tokens=tokens,
                        render_scale=_OCR_RENDER_SCALE,
                        # Bound now so the page index and path are fixed,
                        # but not called unless something needs a crop.
                        page_image=partial(_render_page, i - 1, path),
                    )
                )

    status = Status.OK_WITH_WARNINGS if warnings else Status.OK
    return ExtractionResult(
        status=status,
        segments=segments,
        error_message="; ".join(warnings) if warnings else None,
    )
