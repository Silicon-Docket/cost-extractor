"""Extracts text segments from a PDF: text layer first, OCR fallback per page."""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pdfplumber.utils.exceptions
import pypdfium2 as pdfium
import pytesseract
from pdfminer.pdfdocument import PDFEncryptionError

from cost_extractor import ocr_setup
from cost_extractor.extractors.base import ExtractionResult, Status, TextSegment

_OCR_RENDER_SCALE = 300 / 72  # ~300 DPI


def _is_password_error(e: pdfplumber.utils.exceptions.PdfminerException) -> bool:
    cause = e.args[0] if e.args else None
    return isinstance(cause, PDFEncryptionError)


_pytesseract_configured = False


def _ensure_pytesseract_configured() -> None:
    global _pytesseract_configured
    if not _pytesseract_configured:
        ocr_setup.configure_pytesseract()
        _pytesseract_configured = True


def _ocr_page(page_index: int, path: Path) -> str:
    _ensure_pytesseract_configured()
    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[page_index]
        bitmap = page.render(scale=_OCR_RENDER_SCALE)
        image = bitmap.to_pil()
        return pytesseract.image_to_string(image, config=ocr_setup.get_tessdata_config())
    finally:
        pdf.close()


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
                ocr_text = _ocr_page(i - 1, path).strip()
            except Exception as e:  # noqa: BLE001 - one bad page must not fail the doc
                warnings.append(f"OCR failed on page {i}: {e}")
                continue

            if ocr_text:
                segments.append(
                    TextSegment(text=ocr_text, location=f"page {i}", provenance="ocr")
                )

    status = Status.OK_WITH_WARNINGS if warnings else Status.OK
    return ExtractionResult(
        status=status,
        segments=segments,
        error_message="; ".join(warnings) if warnings else None,
    )
