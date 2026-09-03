"""Builds the .xlsx report (Summary + Details sheets) from a PipelineResult."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from openpyxl import Workbook

from cost_extractor.pipeline import PipelineResult

_SUMMARY_HEADER = [
    "Document",
    "Status",
    "Amounts Found",
    "Subtotal",
    "Message",
    "Review",
]
_DETAILS_HEADER = [
    "Source File",
    "Location",
    "Matched Text",
    "Rule",
    "Value",
    "Source",
    "Confidence",
    "Review",
    # What OCR originally produced, kept beside the corrected value so a
    # correction reads as a correction rather than a silent rewrite.
    "Read As Text",
]

# Written where a value would otherwise be silently trustworthy-looking.
REVIEW_FLAG = "REVIEW"


def review_label(match) -> Optional[str]:
    """What to say about one amount's trustworthiness, in one place.

    Shared with the GUI so the app and the spreadsheet can never disagree
    about whether something counts as checked, corrected, or doubtful.
    None means "nothing worth saying"; a caller rendering into a table cell
    turns that into a blank.
    """
    if match.reviewed:
        return "corrected" if match.corrected_value != match.value else "checked"
    return REVIEW_FLAG if match.needs_review else None


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
                _as_number(doc.effective_subtotal),
                doc.message,
                REVIEW_FLAG if doc.needs_review else None,
            ]
        )

    # Grand Total keeps meaning the whole batch. The two lines under it say
    # how much of that number rests on a doubtful reading, rather than
    # quietly redefining the headline figure.
    summary_ws.append(
        ["Grand Total", None, None, _as_number(result.effective_grand_total), None]
    )
    summary_ws.append(
        ["Of which needs review", None, None, _as_number(result.review_total), None]
    )
    summary_ws.append(
        ["Confidently read", None, None, _as_number(result.confident_total), None]
    )
    # Counts every unchecked guess, not just low-confidence ones: OCR read
    # $940.00 as $440.00 at 84% confidence, so a score cannot certify a
    # reading as safe.
    summary_ws.append(
        [
            "Guessed amounts not yet checked",
            None,
            None,
            result.unreviewed_ocr_count,
            None,
        ]
    )

    details_ws = wb.create_sheet("Details")
    details_ws.append(_DETAILS_HEADER)
    for doc in result.documents:
        for m in doc.matches:
            details_ws.append(
                [
                    m.display_name,
                    m.location,
                    m.raw_text,
                    m.rule_id,
                    _as_number(m.effective_value),
                    m.provenance,
                    m.confidence,
                    review_label(m),
                    m.raw_text if m.reviewed else None,
                ]
            )

    return wb


def save_workbook(wb: Workbook, path: Path) -> None:
    wb.save(path)
