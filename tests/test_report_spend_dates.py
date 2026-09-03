"""Spend-date columns, Summary row, Revisions Dimension rows, and the
Spend By Month sheet."""

from datetime import date, datetime, timezone
from decimal import Decimal

import openpyxl

from cost_extractor.date_rules import default_rules as default_date_rules
from cost_extractor.extractors.base import Status
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult
from cost_extractor.report import build_workbook, save_workbook
from cost_extractor.revisions import record_revision

_NOW = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)


def _match(value="100.00", doc_offset=0) -> MatchRecord:
    return MatchRecord(
        display_name="scan.pdf",
        location="page 1",
        raw_text=f"${value}",
        rule_id="standard",
        value=Decimal(value),
        doc_offset=doc_offset,
    )


def _result(matches, full_text="") -> PipelineResult:
    return PipelineResult.from_documents(
        [
            DocumentResult(
                display_name="scan.pdf",
                status=Status.OK,
                matches=matches,
                subtotal=sum((m.value for m in matches), Decimal("0")),
                full_text=full_text,
            )
        ]
    )


def _sheet(tmp_path, result, name, rules=None):
    path = tmp_path / "report.xlsx"
    save_workbook(build_workbook(result, rules), path)
    return openpyxl.load_workbook(path)[name]


def test_build_workbook_with_no_date_rules_argument_still_produces_details(tmp_path):
    # The default-None path -- every pre-existing single-argument call
    # site keeps compiling and behaving as before.
    ws = _sheet(tmp_path, _result([_match()]), "Details")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Spend Date")] == "Undated"
    assert row[header.index("Spend Date Review")] == "REVIEW"


def test_details_reports_a_confirmed_date(tmp_path):
    m = _match()
    record_revision(m.spend_date_revisions, date(2026, 6, 14), now=_NOW)
    ws = _sheet(tmp_path, _result([m]), "Details")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Spend Date")] == "2026-06-14"
    assert row[header.index("Spend Date Review")] is None


def test_details_reports_a_confirmed_no_date(tmp_path):
    m = _match()
    record_revision(m.spend_date_revisions, None, now=_NOW)
    ws = _sheet(tmp_path, _result([m]), "Details")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Spend Date")] == "No Date (confirmed)"
    assert row[header.index("Spend Date Review")] is None


def test_details_reports_a_suggested_unconfirmed_date(tmp_path):
    full_text = "Dated 06/14/2026, amount $100.00."
    m = _match(doc_offset=full_text.index("$100.00"))
    ws = _sheet(
        tmp_path, _result([m], full_text=full_text), "Details", rules=default_date_rules()
    )
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Spend Date")] == "2026-06-14 (suggested, unconfirmed)"
    assert row[header.index("Spend Date Review")] == "REVIEW"


def test_summary_reports_dates_not_yet_reviewed(tmp_path):
    reviewed = _match()
    record_revision(reviewed.spend_date_revisions, date(2026, 6, 14), now=_NOW)
    unreviewed = _match()
    ws = _sheet(tmp_path, _result([reviewed, unreviewed]), "Summary")
    labels = {row[0].value: row[3].value for row in ws.iter_rows()}

    assert labels["Dates Not Yet Reviewed"] == 1
