"""Every match carries where it came from and how much to trust it.

A printed amount and an OCR-guessed one currently land in the same total
with nothing to tell them apart. These pin the evidence a match must carry
so a low-confidence reading is visible instead of silently summed.
"""

from decimal import Decimal

from cost_extractor.extractors.base import Status
from cost_extractor.money_parser import default_rules
from cost_extractor.pipeline import (
    LOW_CONFIDENCE_THRESHOLD,
    DocumentResult,
    MatchRecord,
    PipelineResult,
    run_pipeline,
)


def test_text_layer_match_has_no_confidence_and_needs_no_review(simple_docx):
    # Nothing was guessed, so there is no score to report — which is not
    # the same as scoring zero.
    result = run_pipeline([simple_docx], default_rules())

    match = result.documents[0].matches[0]
    assert match.provenance == "text"
    assert match.confidence is None
    assert match.bbox is None
    assert match.value_needs_review is False


def test_ocr_match_carries_confidence_and_a_box(
    scanned_image_pdf, skip_if_no_tesseract
):
    result = run_pipeline([scanned_image_pdf], default_rules())

    match = next(
        m for m in result.documents[0].matches if m.value == Decimal("2345.00")
    )
    assert match.provenance == "ocr"
    assert 0 < match.confidence <= 100
    assert match.bbox.width > 0
    assert match.render_scale is not None


def test_a_confident_ocr_match_does_not_need_review(
    scanned_image_pdf, skip_if_no_tesseract
):
    # Clean rendered text should read well above the threshold; if this
    # starts failing, the threshold is wrong, not the fixture.
    result = run_pipeline([scanned_image_pdf], default_rules())

    match = next(
        m for m in result.documents[0].matches if m.value == Decimal("2345.00")
    )
    assert match.confidence >= LOW_CONFIDENCE_THRESHOLD
    assert match.value_needs_review is False


def test_grand_total_still_means_everything_combined(simple_docx):
    # Downstream consumers (the packaged --selftest smoke test among them)
    # read grand_total as the whole batch. Splitting it in place would
    # quietly change what they assert.
    result = run_pipeline([simple_docx], default_rules())

    assert result.grand_total == Decimal("1734.56")
    assert result.confident_total + result.review_total == result.grand_total


def _match(value: str, confidence=None) -> MatchRecord:
    return MatchRecord(
        display_name="scan.pdf",
        location="page 1",
        raw_text=f"${value}",
        rule_id="standard",
        value=Decimal(value),
        provenance="text" if confidence is None else "ocr",
        confidence=confidence,
    )


def _document(matches: list[MatchRecord]) -> DocumentResult:
    return DocumentResult(
        display_name="scan.pdf",
        status=Status.OK,
        matches=matches,
        subtotal=sum((m.value for m in matches), Decimal("0")),
    )


def test_low_confidence_amounts_are_totalled_apart_from_the_rest():
    # Constructed rather than OCR'd: a fixture that happens to read badly
    # today could read well tomorrow and stop testing this at all.
    result = PipelineResult.from_documents(
        [
            _document(
                [
                    _match("100.00"),  # printed, no score
                    _match("200.00", confidence=95.0),
                    _match("40.00", confidence=31.0),  # misread risk
                ]
            )
        ]
    )

    assert result.review_total == Decimal("40.00")
    assert result.confident_total == Decimal("300.00")


def test_the_headline_total_still_includes_amounts_under_review():
    # The split is there to show what is shaky, not to quietly drop it.
    result = PipelineResult.from_documents(
        [_document([_match("200.00", confidence=95.0), _match("40.00", confidence=31.0)])]
    )

    assert result.grand_total == Decimal("240.00")


def test_a_document_flags_itself_when_any_match_needs_review():
    doc = _document([_match("200.00", confidence=95.0), _match("40.00", confidence=31.0)])

    assert doc.needs_review is True


def test_a_document_with_only_trusted_matches_is_not_flagged():
    doc = _document([_match("200.00", confidence=95.0), _match("100.00")])

    assert doc.needs_review is False


def test_a_match_exactly_at_the_threshold_is_trusted():
    # The threshold is the lowest score still considered readable, so the
    # boundary belongs on the confident side.
    assert _match("10.00", confidence=LOW_CONFIDENCE_THRESHOLD).value_needs_review is False
