"""The report has to distinguish a read amount from a guessed one."""

from decimal import Decimal

import openpyxl

from cost_extractor.extractors.base import Status
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult
from cost_extractor.report import build_workbook, save_workbook


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


def _result() -> PipelineResult:
    return PipelineResult.from_documents(
        [
            DocumentResult(
                display_name="scan.pdf",
                status=Status.OK,
                matches=[
                    _match("100.00"),
                    _match("200.00", confidence=95.0),
                    _match("40.00", confidence=31.0),
                ],
                subtotal=Decimal("340.00"),
            )
        ]
    )


def _sheet(tmp_path, name):
    path = tmp_path / "report.xlsx"
    save_workbook(build_workbook(_result()), path)
    return openpyxl.load_workbook(path)[name]


def test_details_sheet_reports_source_and_confidence(tmp_path):
    ws = _sheet(tmp_path, "Details")

    header = [c.value for c in ws[1]]
    assert "Source" in header
    assert "Confidence" in header


def test_a_read_amount_reports_its_source_with_no_score(tmp_path):
    ws = _sheet(tmp_path, "Details")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Source")] == "text"
    # A blank cell, not a zero: nothing was guessed, so there is no score.
    assert row[header.index("Confidence")] is None


def test_a_guessed_amount_reports_its_score(tmp_path):
    ws = _sheet(tmp_path, "Details")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[3]]

    assert row[header.index("Source")] == "ocr"
    assert row[header.index("Confidence")] == 95.0


def test_a_doubtful_amount_is_marked_for_review(tmp_path):
    ws = _sheet(tmp_path, "Details")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[4]]

    assert row[header.index("Review")] == "REVIEW"


def test_a_trusted_amount_is_not_marked_for_review(tmp_path):
    ws = _sheet(tmp_path, "Details")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[3]]

    assert row[header.index("Review")] is None


def test_summary_splits_the_total_without_losing_the_combined_one(tmp_path):
    ws = _sheet(tmp_path, "Summary")

    labels = {row[0].value: row[3].value for row in ws.iter_rows()}
    assert labels["Grand Total"] == 340.0
    assert labels["Of which needs review"] == 40.0


def test_summary_flags_a_document_that_needs_review(tmp_path):
    ws = _sheet(tmp_path, "Summary")

    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]
    assert row[header.index("Review")] == "REVIEW"


def _corrected_result() -> PipelineResult:
    read_wrong = _match("440.00", confidence=84.0)
    read_wrong.corrected_value = Decimal("940.00")
    checked = _match("200.00", confidence=95.0)
    checked.corrected_value = Decimal("200.00")
    return PipelineResult.from_documents(
        [
            DocumentResult(
                display_name="scan.pdf",
                status=Status.OK,
                matches=[read_wrong, checked],
                subtotal=Decimal("640.00"),
            )
        ]
    )


def _corrected_sheet(tmp_path, name):
    path = tmp_path / "corrected.xlsx"
    save_workbook(build_workbook(_corrected_result()), path)
    return openpyxl.load_workbook(path)[name]


def test_a_correction_reaches_the_exported_value(tmp_path):
    ws = _corrected_sheet(tmp_path, "Details")
    header = [c.value for c in ws[1]]

    assert [c.value for c in ws[2]][header.index("Value")] == 940.0


def test_the_original_reading_is_still_exported(tmp_path):
    # A correction has to be visible as a correction, not a silent rewrite.
    ws = _corrected_sheet(tmp_path, "Details")
    header = [c.value for c in ws[1]]

    assert [c.value for c in ws[2]][header.index("Read As Text")] == "$440.00"


def test_a_corrected_row_says_so(tmp_path):
    ws = _corrected_sheet(tmp_path, "Details")
    header = [c.value for c in ws[1]]

    assert [c.value for c in ws[2]][header.index("Review")] == "corrected"
    assert [c.value for c in ws[3]][header.index("Review")] == "checked"


def test_the_grand_total_reflects_corrections(tmp_path):
    ws = _corrected_sheet(tmp_path, "Summary")
    labels = {row[0].value: row[3].value for row in ws.iter_rows()}

    assert labels["Grand Total"] == 1140.0  # 940 + 200, not 640


def test_the_summary_reports_what_is_still_unchecked(tmp_path):
    ws = _sheet(tmp_path, "Summary")
    labels = {row[0].value: row[3].value for row in ws.iter_rows()}

    # Two OCR amounts in _result(), neither reviewed.
    assert labels["Guessed amounts not yet checked"] == 2
