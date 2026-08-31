"""Builds the .xlsx report (Summary + Details sheets) from a PipelineResult."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from cost_extractor.pipeline import PipelineResult

_SUMMARY_HEADER = ["Document", "Status", "Amounts Found", "Subtotal", "Message"]
_DETAILS_HEADER = ["Source File", "Location", "Matched Text", "Rule", "Value"]


def _as_number(value) -> float:
    # Written as a plain float, never an Excel formula string, so a
    # reload via openpyxl (which does not evaluate formulas) sees the
    # real computed value.
    return float(value)


def build_workbook(result: PipelineResult) -> Workbook:
    wb = Workbook()
    summary_ws = wb.active
    summary_ws.title = "Summary"
    summary_ws.append(_SUMMARY_HEADER)

    for doc in result.documents:
        summary_ws.append(
            [
                doc.display_name,
                doc.status.value,
                len(doc.matches),
                _as_number(doc.subtotal),
                doc.message,
            ]
        )

    summary_ws.append(["Grand Total", None, None, _as_number(result.grand_total), None])

    details_ws = wb.create_sheet("Details")
    details_ws.append(_DETAILS_HEADER)
    for doc in result.documents:
        for m in doc.matches:
            details_ws.append(
                [m.display_name, m.location, m.raw_text, m.rule_id, _as_number(m.value)]
            )

    return wb


def save_workbook(wb: Workbook, path: Path) -> None:
    wb.save(path)
