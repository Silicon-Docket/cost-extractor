from decimal import Decimal

from cost_extractor.extractors.base import Status
from cost_extractor.extractors.pdf_extractor import extract
from cost_extractor.money_parser import default_rules, find_money_matches


def test_extract_returns_ok_status_for_text_pdf(text_pdf):
    result = extract(text_pdf, ocr_enabled=False)

    assert result.status == Status.OK


def test_extract_captures_text_layer_content(text_pdf):
    result = extract(text_pdf, ocr_enabled=False)

    texts = [seg.text for seg in result.segments]
    assert any("$1,234.56" in t for t in texts)


def test_extract_text_segments_have_text_provenance(text_pdf):
    result = extract(text_pdf, ocr_enabled=False)

    money_segment = next(seg for seg in result.segments if "$1,234.56" in seg.text)
    assert money_segment.provenance == "text"


def test_extract_password_protected_pdf_returns_distinct_error(password_protected_pdf):
    result = extract(password_protected_pdf, ocr_enabled=False)

    assert result.status == Status.ERROR
    assert "password" in result.error_message.lower()


def test_extract_scanned_pdf_without_ocr_yields_no_text_segments(scanned_image_pdf):
    result = extract(scanned_image_pdf, ocr_enabled=False)

    assert result.status == Status.OK
    assert result.segments == []


def test_extract_scanned_pdf_with_ocr_falls_back_and_finds_amount(
    scanned_image_pdf, skip_if_no_tesseract
):
    result = extract(scanned_image_pdf, ocr_enabled=True)

    assert result.status == Status.OK
    ocr_segments = [seg for seg in result.segments if seg.provenance == "ocr"]
    assert ocr_segments, "expected at least one OCR-derived segment"

    all_text = " ".join(seg.text for seg in ocr_segments)
    matches = find_money_matches(all_text, default_rules())
    assert any(m.value == Decimal("2345.00") for m in matches)
