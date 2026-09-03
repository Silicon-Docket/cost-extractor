"""The offset -> pixel/confidence mapping that makes crop review possible.

`MoneyMatch` already reports character offsets into a segment's text. If OCR
records where each token sat on the page, a match maps back to a crop region
and a trust score without a second parsing pass.
"""

from cost_extractor.extractors.base import (
    BoundingBox,
    PositionedToken,
    TextSegment,
    evidence_for_span,
)


def _segment(text: str, tokens: list[PositionedToken]) -> TextSegment:
    return TextSegment(text=text, location="page 1", provenance="ocr", tokens=tokens)


def test_span_covering_one_token_reports_that_tokens_box_and_confidence():
    seg = _segment(
        "Total $940.00",
        [
            PositionedToken("Total", 0, 5, BoundingBox(10, 20, 50, 12), 96.0),
            PositionedToken("$940.00", 6, 13, BoundingBox(70, 20, 80, 12), 54.0),
        ],
    )

    evidence = evidence_for_span(seg, 6, 13)

    assert evidence.bbox == BoundingBox(70, 20, 80, 12)
    assert evidence.confidence == 54.0


def test_span_across_two_tokens_unions_the_boxes():
    seg = _segment(
        "$ 940.00",
        [
            PositionedToken("$", 0, 1, BoundingBox(10, 20, 10, 12), 88.0),
            PositionedToken("940.00", 2, 8, BoundingBox(30, 18, 60, 16), 61.0),
        ],
    )

    evidence = evidence_for_span(seg, 0, 8)

    # Union spans from the left edge of the first box to the right edge of
    # the second, and from the highest top to the lowest bottom.
    assert evidence.bbox == BoundingBox(10, 18, 80, 16)


def test_span_across_two_tokens_takes_the_worst_confidence():
    # An amount is only as trustworthy as its worst-read digit, so the
    # union must not average away a bad token.
    seg = _segment(
        "$ 940.00",
        [
            PositionedToken("$", 0, 1, BoundingBox(10, 20, 10, 12), 88.0),
            PositionedToken("940.00", 2, 8, BoundingBox(30, 18, 60, 16), 61.0),
        ],
    )

    evidence = evidence_for_span(seg, 0, 8)

    assert evidence.confidence == 61.0


def test_partial_overlap_still_counts_the_token():
    # A regex match can start mid-token; that token was still read to
    # produce the characters the match consumed.
    seg = _segment(
        "USD940.00",
        [PositionedToken("USD940.00", 0, 9, BoundingBox(10, 20, 90, 12), 73.0)],
    )

    evidence = evidence_for_span(seg, 3, 9)

    assert evidence.bbox == BoundingBox(10, 20, 90, 12)
    assert evidence.confidence == 73.0


def test_segment_from_the_text_layer_has_no_evidence():
    # Text-layer segments carry no tokens: there is no bitmap to crop and
    # no confidence to report, which is different from "confidence zero".
    seg = TextSegment(text="Total $940.00", location="page 1")

    assert evidence_for_span(seg, 6, 13) is None


def test_span_touching_no_token_has_no_evidence():
    seg = _segment(
        "Total $940.00",
        [PositionedToken("Total", 0, 5, BoundingBox(10, 20, 50, 12), 96.0)],
    )

    assert evidence_for_span(seg, 6, 13) is None


def test_zero_width_span_touching_no_token_has_no_evidence():
    seg = _segment(
        "ab",
        [PositionedToken("ab", 0, 2, BoundingBox(10, 20, 20, 12), 90.0)],
    )

    # An empty span sits between characters and consumed nothing, so it
    # must not silently borrow a neighbouring token's box.
    assert evidence_for_span(seg, 2, 2) is None
