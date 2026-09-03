"""Standalone images as first-class documents.

A photographed or scanned receipt arrives as a .jpg/.png, not a PDF. These
were previously dropped before extraction ever ran.
"""

from decimal import Decimal

import pytest

from cost_extractor.extractors.base import Status, evidence_for_span
from cost_extractor.extractors.image_extractor import extract
from cost_extractor.money_parser import default_rules, find_money_matches


def test_extract_reads_an_amount_off_an_image(scan_image, skip_if_no_tesseract):
    result = extract(scan_image)

    assert result.status == Status.OK
    matches = find_money_matches(result.segments[0].text, default_rules())
    assert any(m.value == Decimal("2345.00") for m in matches)


def test_image_segments_are_marked_as_ocr_derived(scan_image, skip_if_no_tesseract):
    result = extract(scan_image)

    assert result.segments[0].provenance == "ocr"


def test_image_segment_locates_the_whole_file_not_a_page(
    scan_image, skip_if_no_tesseract
):
    # There are no pages to number, and "page 1" would imply a container
    # the user could go look at.
    result = extract(scan_image)

    assert result.segments[0].location == "image"


def test_image_boxes_are_in_native_pixels(scan_image, skip_if_no_tesseract):
    # Unlike a PDF page there is no rendering step, so a crop means simply
    # reopening the file at its native size.
    result = extract(scan_image)

    assert result.segments[0].render_scale == 1.0


def test_a_matched_amount_on_an_image_maps_back_to_a_box(
    scan_image, skip_if_no_tesseract
):
    result = extract(scan_image)

    segment = result.segments[0]
    match = next(
        m
        for m in find_money_matches(segment.text, default_rules())
        if m.value == Decimal("2345.00")
    )
    evidence = evidence_for_span(segment, match.start, match.end)

    assert evidence is not None
    assert evidence.bbox.width > 0 and evidence.bbox.height > 0


def test_ocr_disabled_yields_no_segments_rather_than_an_error(scan_image):
    # An image has no text layer to fall back to, so switching OCR off
    # leaves nothing to read — but that is not a failure to report.
    result = extract(scan_image, ocr_enabled=False)

    assert result.status == Status.OK
    assert result.segments == []


def test_corrupt_image_is_a_hard_error(corrupt_image):
    result = extract(corrupt_image)

    assert result.status == Status.ERROR
    assert "unreadable" in result.error_message.lower()


def test_blank_image_yields_no_segments(tmp_path, skip_if_no_tesseract):
    from PIL import Image

    path = tmp_path / "blank.png"
    Image.new("RGB", (400, 200), "white").save(path)

    result = extract(path)

    assert result.status == Status.OK
    assert result.segments == []


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"])
def test_common_scan_formats_are_supported(suffix):
    from cost_extractor.ingestion import SUPPORTED_SUFFIXES

    assert suffix in SUPPORTED_SUFFIXES
