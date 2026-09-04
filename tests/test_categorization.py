"""A human-confirmed spend category, and the line-scoped suggestion it's
based on."""

from datetime import datetime, timezone
from decimal import Decimal

from cost_extractor.extractors.base import Status
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult, _line_containing
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


def test_line_containing_a_match_on_the_first_line():
    text = "Materials: $100.00\nLabor: $200.00\nTravel: $50.00"

    assert _line_containing(text, text.index("$100.00")) == "Materials: $100.00"


def test_line_containing_a_match_on_a_middle_line():
    text = "Materials: $100.00\nLabor: $200.00\nTravel: $50.00"

    assert _line_containing(text, text.index("$200.00")) == "Labor: $200.00"


def test_line_containing_a_match_on_the_last_line_with_no_trailing_newline():
    text = "Materials: $100.00\nLabor: $200.00\nTravel: $50.00"

    assert _line_containing(text, text.index("$50.00")) == "Travel: $50.00"


def test_line_containing_a_single_line_segment():
    text = "Just one line: $100.00 total"

    assert _line_containing(text, text.index("$100.00")) == text


def test_a_never_reviewed_match_is_not_category_reviewed():
    m = _match()

    assert m.category_reviewed is False
    assert m.effective_category is None


def test_confirming_a_category_marks_it_reviewed():
    m = _match()

    record_revision(m.category_revisions, "Materials", now=_NOW)

    assert m.category_reviewed is True
    assert m.effective_category == "Materials"


def test_a_second_category_confirmation_preserves_the_first_as_history():
    m = _match()
    first = datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc)
    second = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)

    record_revision(m.category_revisions, "Materials", now=first)
    record_revision(m.category_revisions, "Labor", note="fixed", now=second)

    assert [r.value for r in m.category_revisions] == ["Materials", "Labor"]
    assert m.effective_category == "Labor"


def test_uncategorized_count_counts_a_never_reviewed_match():
    result = _result([_match()])

    assert result.uncategorized_count == 1


def test_uncategorized_count_excludes_a_confirmed_category():
    m = _match()
    record_revision(m.category_revisions, "Materials", now=_NOW)
    result = _result([m])

    assert result.uncategorized_count == 0


def test_uncategorized_count_is_independent_of_unreviewed_ocr_count():
    # An OCR-derived match that's category-confirmed but NOT value-reviewed
    # must still count as categorized -- proving uncategorized_count uses
    # its own filter (category_reviewed), not accidentally reusing
    # unreviewed_ocr_count's (value_reviewed + provenance == "ocr").
    m = MatchRecord(
        display_name="scan.pdf",
        location="page 1",
        raw_text="$40.00",
        rule_id="standard",
        value=Decimal("40.00"),
        provenance="ocr",
        confidence=31.0,
    )
    record_revision(m.category_revisions, "Materials", now=_NOW)
    result = _result([m])

    assert result.uncategorized_count == 0
    assert result.unreviewed_ocr_count == 1


def test_process_single_file_captures_line_text(monkeypatch):
    from cost_extractor.extractors.base import ExtractionResult, TextSegment
    from cost_extractor.ingestion import DiscoveredFile
    from cost_extractor.money_parser import default_rules
    from cost_extractor import pipeline as pipeline_module

    segment_text = "Materials: $100.00\nLabor: $200.00\nTravel: $50.00"
    segments = [TextSegment(text=segment_text, location="page 1")]
    fake_extraction = ExtractionResult(status=Status.OK, segments=segments)
    monkeypatch.setattr(
        pipeline_module, "_extract", lambda discovered, ocr_enabled: fake_extraction
    )
    discovered = DiscoveredFile(display_name="fake.docx", suffix=".docx", status=None)

    doc = pipeline_module._process_single_file(discovered, default_rules(), ocr_enabled=True)

    by_value = {m.raw_text: m.line_text for m in doc.matches}
    assert by_value["$100.00"] == "Materials: $100.00"
    assert by_value["$200.00"] == "Labor: $200.00"
    assert by_value["$50.00"] == "Travel: $50.00"
