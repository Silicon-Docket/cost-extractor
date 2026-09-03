"""A human correction overrides what OCR guessed, everywhere.

Reviewing a crop is pointless if fixing the number doesn't change the total.
"""

from datetime import datetime, timezone
from decimal import Decimal

from cost_extractor.extractors.base import Status
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult
from cost_extractor.revisions import record_revision

_NOW = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)


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


def _result(matches) -> PipelineResult:
    return PipelineResult.from_documents(
        [
            DocumentResult(
                display_name="scan.pdf",
                status=Status.OK,
                matches=matches,
                subtotal=sum((m.value for m in matches), Decimal("0")),
            )
        ]
    )


def test_an_uncorrected_match_counts_as_read():
    m = _match("340.00", confidence=84.0)

    assert m.effective_value == Decimal("340.00")


def test_a_correction_replaces_the_read_value():
    # The $940 -> $440 case: read confidently, and still wrong.
    m = _match("440.00", confidence=84.0)

    record_revision(m.value_revisions, Decimal("940.00"), now=_NOW)

    assert m.effective_value == Decimal("940.00")
    assert m.value == Decimal("440.00"), "the original reading is kept for the record"


def test_a_correction_moves_the_document_subtotal():
    matches = [_match("440.00", confidence=84.0), _match("100.00")]
    result = _result(matches)
    assert result.documents[0].effective_subtotal == Decimal("540.00")

    record_revision(matches[0].value_revisions, Decimal("940.00"), now=_NOW)

    assert result.documents[0].effective_subtotal == Decimal("1040.00")


def test_a_correction_moves_the_grand_total():
    matches = [_match("440.00", confidence=84.0)]
    result = _result(matches)

    record_revision(matches[0].value_revisions, Decimal("940.00"), now=_NOW)

    assert result.effective_grand_total == Decimal("940.00")


def test_a_reviewed_match_no_longer_needs_review():
    m = _match("40.00", confidence=31.0)
    assert m.value_needs_review is True

    record_revision(m.value_revisions, Decimal("940.00"), now=_NOW)

    assert m.value_needs_review is False


def test_confirming_a_reading_without_changing_it_also_clears_review():
    # Accepting what OCR read is a judgement too; it must not stay flagged.
    m = _match("40.00", confidence=31.0)

    record_revision(m.value_revisions, m.value, now=_NOW)

    assert m.value_needs_review is False
    assert m.effective_value == Decimal("40.00")


def test_a_correction_of_zero_is_honoured_not_treated_as_absent():
    # Deleting a spurious amount OCR invented is a legitimate correction.
    m = _match("5340.00", confidence=84.0)

    record_revision(m.value_revisions, Decimal("0"), now=_NOW)

    assert m.effective_value == Decimal("0")
    assert m.value_needs_review is False


def test_raw_grand_total_still_reports_what_was_read():
    # The unedited figure stays available, so a correction is visibly a
    # correction rather than a silent rewrite of history.
    matches = [_match("440.00", confidence=84.0)]
    result = _result(matches)

    record_revision(matches[0].value_revisions, Decimal("940.00"), now=_NOW)

    assert result.grand_total == Decimal("440.00")
    assert result.effective_grand_total == Decimal("940.00")


def test_the_three_summary_totals_still_add_up_after_a_correction():
    # The Summary sheet prints all three. If Grand Total moves with
    # corrections but Confidently read does not, the report contradicts
    # itself in front of the user.
    matches = [_match("440.00", confidence=84.0), _match("40.00", confidence=31.0)]
    result = _result(matches)

    record_revision(matches[0].value_revisions, Decimal("940.00"), now=_NOW)

    assert (
        result.confident_total + result.review_total == result.effective_grand_total
    )


def test_correcting_an_amount_moves_it_out_of_the_review_total():
    matches = [_match("40.00", confidence=31.0)]
    result = _result(matches)
    assert result.review_total == Decimal("40.00")

    record_revision(matches[0].value_revisions, Decimal("940.00"), now=_NOW)

    assert result.review_total == Decimal("0")
    assert result.confident_total == Decimal("940.00")


def test_a_second_correction_preserves_the_first_as_history():
    # The scenario this sub-project exists to support: fixing your own
    # mistake must not erase that the first correction ever happened.
    m = _match("440.00", confidence=84.0)
    first = datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc)
    second = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)

    record_revision(m.value_revisions, Decimal("900.00"), now=first)
    record_revision(m.value_revisions, Decimal("940.00"), note="fixed typo", now=second)

    assert [r.value for r in m.value_revisions] == [Decimal("900.00"), Decimal("940.00")]
    assert m.value_revisions[0].at == first
    assert m.value_revisions[1].at == second
    assert m.value_revisions[1].note == "fixed typo"
    assert m.effective_value == Decimal("940.00")
