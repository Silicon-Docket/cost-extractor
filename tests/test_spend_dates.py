"""A human-confirmed spend date, and the rollups that depend on it."""

from datetime import date, datetime, timezone
from decimal import Decimal

from cost_extractor.extractors.base import ExtractionResult, Status, TextSegment
from cost_extractor.ingestion import DiscoveredFile
from cost_extractor.money_parser import default_rules
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult
from cost_extractor.revisions import record_revision

_NOW = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)


def _match(value: str = "100.00") -> MatchRecord:
    return MatchRecord(
        display_name="scan.pdf",
        location="page 1",
        raw_text=f"${value}",
        rule_id="standard",
        value=Decimal(value),
    )


def _result(matches: list[MatchRecord]) -> PipelineResult:
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


def test_a_never_reviewed_match_is_not_spend_date_reviewed():
    m = _match()

    assert m.spend_date_reviewed is False
    assert m.effective_spend_date is None


def test_confirming_a_date_marks_it_reviewed():
    m = _match()

    record_revision(m.spend_date_revisions, date(2026, 6, 14), now=_NOW)

    assert m.spend_date_reviewed is True
    assert m.effective_spend_date == date(2026, 6, 14)


def test_confirming_no_date_still_marks_it_reviewed():
    # A deliberate "no date applies" decision (App.confirm_no_date) is a
    # completed review, not a missing one -- spend_date_reviewed must be
    # True even though effective_spend_date stays None.
    m = _match()

    record_revision(m.spend_date_revisions, None, now=_NOW, note="confirmed no associated date")

    assert m.spend_date_reviewed is True
    assert m.effective_spend_date is None


def test_a_second_date_confirmation_preserves_the_first_as_history():
    m = _match()
    first = datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc)
    second = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)

    record_revision(m.spend_date_revisions, date(2026, 6, 1), now=first)
    record_revision(m.spend_date_revisions, date(2026, 6, 14), note="fixed", now=second)

    assert [r.value for r in m.spend_date_revisions] == [date(2026, 6, 1), date(2026, 6, 14)]
    assert m.effective_spend_date == date(2026, 6, 14)


def test_unreviewed_date_count_counts_a_never_reviewed_match():
    result = _result([_match()])

    assert result.unreviewed_date_count == 1


def test_unreviewed_date_count_excludes_a_confirmed_date():
    m = _match()
    record_revision(m.spend_date_revisions, date(2026, 6, 14), now=_NOW)
    result = _result([m])

    assert result.unreviewed_date_count == 0


def test_unreviewed_date_count_excludes_a_confirmed_no_date():
    # A deliberate "none" is still a completed review -- must not be
    # double-counted as still-needing-attention.
    m = _match()
    record_revision(m.spend_date_revisions, None, now=_NOW)
    result = _result([m])

    assert result.unreviewed_date_count == 0


def test_process_single_file_computes_doc_offset_and_full_text(monkeypatch):
    from cost_extractor import pipeline as pipeline_module

    segment1_text = "No amounts here."
    segment2_text = "Amount: $100.00 due."
    segment3_text = "Nothing else."
    segments = [
        TextSegment(text=segment1_text, location="page 1"),
        TextSegment(text=segment2_text, location="page 2"),
        TextSegment(text=segment3_text, location="page 3"),
    ]
    fake_extraction = ExtractionResult(status=Status.OK, segments=segments)
    monkeypatch.setattr(
        pipeline_module, "_extract", lambda discovered, ocr_enabled: fake_extraction
    )
    discovered = DiscoveredFile(display_name="fake.docx", suffix=".docx", status=None)

    doc = pipeline_module._process_single_file(discovered, default_rules(), ocr_enabled=True)

    assert doc.full_text == segment1_text + "\n\n" + segment2_text + "\n\n" + segment3_text
    assert len(doc.matches) == 1
    local_start = segment2_text.index("$100.00")
    expected_offset = len(segment1_text) + 2 + local_start  # +2 for the "\n\n" separator
    assert doc.matches[0].doc_offset == expected_offset


def test_a_single_segment_document_has_full_text_equal_to_that_segment(monkeypatch):
    from cost_extractor import pipeline as pipeline_module

    segments = [TextSegment(text="Just one segment, $50.00 total.", location="page 1")]
    fake_extraction = ExtractionResult(status=Status.OK, segments=segments)
    monkeypatch.setattr(
        pipeline_module, "_extract", lambda discovered, ocr_enabled: fake_extraction
    )
    discovered = DiscoveredFile(display_name="fake.docx", suffix=".docx", status=None)

    doc = pipeline_module._process_single_file(discovered, default_rules(), ocr_enabled=True)

    assert doc.full_text == "Just one segment, $50.00 total."
    assert doc.matches[0].doc_offset == doc.full_text.index("$50.00")
