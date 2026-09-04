"""Category columns, Summary row, Revisions Dimension rows, and the
Categories sheet."""

from datetime import datetime, timezone
from decimal import Decimal

import openpyxl

from cost_extractor.extractors.base import Status
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult
from cost_extractor.report import build_workbook, save_workbook
from cost_extractor.revisions import record_revision

_NOW = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)


def _match(value="100.00", line_text="") -> MatchRecord:
    return MatchRecord(
        display_name="scan.pdf",
        location="page 1",
        raw_text=f"${value}",
        rule_id="standard",
        value=Decimal(value),
        line_text=line_text,
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


def _sheet(tmp_path, result, name, rules=None):
    path = tmp_path / "report.xlsx"
    save_workbook(build_workbook(result, rules), path)
    return openpyxl.load_workbook(path)[name]


def test_build_workbook_with_no_category_rules_argument_still_produces_details(tmp_path):
    # The default-None path -- every pre-existing single-argument call
    # site keeps compiling and behaving as before.
    ws = _sheet(tmp_path, _result([_match()]), "Details")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Category")] == "Uncategorized"
    assert row[header.index("Category Review")] == "REVIEW"


def test_details_reports_a_confirmed_category(tmp_path):
    m = _match()
    record_revision(m.category_revisions, "Materials", now=_NOW)
    ws = _sheet(tmp_path, _result([m]), "Details")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Category")] == "Materials"
    assert row[header.index("Category Review")] is None


def test_details_reports_a_suggested_unconfirmed_category(tmp_path):
    from cost_extractor.category_rules import default_rules

    m = _match(line_text="materials delivered")
    ws = _sheet(tmp_path, _result([m]), "Details", rules=default_rules())
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Category")] == "Materials (suggested, unconfirmed)"
    assert row[header.index("Category Review")] == "REVIEW"


def test_summary_reports_amounts_not_yet_categorized(tmp_path):
    categorized = _match()
    record_revision(categorized.category_revisions, "Materials", now=_NOW)
    uncategorized = _match()
    ws = _sheet(tmp_path, _result([categorized, uncategorized]), "Summary")
    labels = {row[0].value: row[3].value for row in ws.iter_rows()}

    assert labels["Amounts not yet categorized"] == 1
