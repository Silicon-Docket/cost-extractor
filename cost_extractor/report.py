"""Builds the .xlsx report (Summary, Details, Revisions, and Spend By Month sheets) from a PipelineResult."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Optional

from openpyxl import Workbook

from cost_extractor.pipeline import PipelineResult
from cost_extractor.revisions import format_revision_timestamp
from cost_extractor import date_rules as _date_rules

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
    "Spend Date",
    "Spend Date Review",
]

# Written where a value would otherwise be silently trustworthy-looking.
REVIEW_FLAG = "REVIEW"


def review_label(match) -> Optional[str]:
    """What to say about one amount's trustworthiness, in one place.

    Shared with the GUI so the app and the spreadsheet can never disagree
    about whether something counts as checked, corrected, or doubtful.
    None means "nothing worth saying"; a caller rendering into a table cell
    turns that into a blank.

    This is a *current-state* label: a match corrected twice that ends
    back at its original value reads "checked", same as one nobody ever
    touched a second time. That's intentional — this answers "does the
    number differ from the machine reading right now"; the Revisions
    sheet answers "what happened, in order", which is a different
    question.
    """
    if match.value_reviewed:
        return "corrected" if match.effective_value != match.value else "checked"
    return REVIEW_FLAG if match.value_needs_review else None


def spend_date_label(
    match, doc: "DocumentResult", rules: list["DateRule"], candidates=None
) -> str:
    if match.spend_date_reviewed:
        # A human can confirm "no date applies" -- that's a completed
        # review, not a missing one, so it gets its own label rather
        # than crashing on effective_spend_date.isoformat() (None has no
        # such method) or reading the same as "nobody has looked yet."
        if match.effective_spend_date is None:
            return "No Date (confirmed)"
        return match.effective_spend_date.isoformat()
    if candidates is None:
        candidates = _date_rules.find_dates(doc.full_text, rules)
    nearest = _date_rules.nearest_date(candidates, match.doc_offset)
    if nearest is None or nearest.value is None:
        return "Undated"
    return f"{nearest.value.isoformat()} (suggested, unconfirmed)"


def _as_number(value) -> float:
    # Written as a plain float, never an Excel formula string, so a
    # reload via openpyxl (which does not evaluate formulas) sees the
    # real computed value.
    return float(value)


_REVISIONS_HEADER = [
    "Source File",
    "Location",
    "Matched Text",
    "Rule",
    "Dimension",
    "Revised From",
    "Revised To",
    "Timestamp",
    "Note",
]


def _revision_rows(match) -> list[list]:
    """One row per revision event for one match, in order.

    "Revised From" is the value immediately before that revision: the
    match's original reading for the first revision, the previous
    revision's value for every one after — so reading down a match's
    rows reconstructs the full chain.
    """
    rows = []
    previous = match.value
    for revision in match.value_revisions:
        rows.append(
            [
                match.display_name,
                match.location,
                match.raw_text,
                match.rule_id,
                "Value",
                _as_number(previous),
                _as_number(revision.value),
                format_revision_timestamp(revision.at),
                revision.note,
            ]
        )
        previous = revision.value
    return rows


def _spend_date_revision_rows(match) -> list[list]:
    """One row per spend-date-revision event, same chaining rule as
    _revision_rows: "Revised From" is the date immediately before that
    revision -- "Undated" for the first one (nothing was ever confirmed
    before it), the previous revision's date (or "Undated" if that one
    was a confirmed no-date) for every one after."""
    rows = []
    previous = None
    for revision in match.spend_date_revisions:
        rows.append(
            [
                match.display_name,
                match.location,
                match.raw_text,
                match.rule_id,
                "Spend Date",
                previous.isoformat() if previous is not None else "Undated",
                revision.value.isoformat() if revision.value is not None else "Undated",
                format_revision_timestamp(revision.at),
                revision.note,
            ]
        )
        previous = revision.value
    return rows


_SPEND_BY_MONTH_HEADER = ["Month", "Amount", "Match Count"]


def _spend_by_month_rows(result: PipelineResult) -> list[list]:
    """One row per calendar month with a confirmed spend date, sorted
    chronologically, plus two final rows so every match lands in exactly
    one bucket: a confirmed "no date applies" is a different fact from a
    match nobody has reviewed yet, so they never share a row."""
    by_month: dict[str, tuple[Decimal, int]] = {}
    no_date_total = Decimal("0")
    no_date_count = 0
    unreviewed_total = Decimal("0")
    unreviewed_count = 0

    for doc in result.documents:
        for m in doc.matches:
            if not m.spend_date_reviewed:
                unreviewed_total += m.effective_value
                unreviewed_count += 1
            elif m.effective_spend_date is None:
                no_date_total += m.effective_value
                no_date_count += 1
            else:
                key = m.effective_spend_date.strftime("%Y-%m")
                total, count = by_month.get(key, (Decimal("0"), 0))
                by_month[key] = (total + m.effective_value, count + 1)

    rows = [
        [month, _as_number(total), count]
        for month, (total, count) in sorted(by_month.items())
    ]
    if no_date_count:
        rows.append(["No Date (confirmed)", _as_number(no_date_total), no_date_count])
    if unreviewed_count:
        rows.append(["Not Yet Reviewed", _as_number(unreviewed_total), unreviewed_count])
    return rows


def build_workbook(
    result: PipelineResult, date_rules: Optional[list["DateRule"]] = None
) -> Workbook:
    active_date_rules = date_rules or []
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
    summary_ws.append(
        [
            "Dates Not Yet Reviewed",
            None,
            None,
            result.unreviewed_date_count,
            None,
        ]
    )

    details_ws = wb.create_sheet("Details")
    details_ws.append(_DETAILS_HEADER)
    for doc in result.documents:
        doc_date_candidates = _date_rules.find_dates(doc.full_text, active_date_rules)
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
                    m.raw_text if m.value_reviewed else None,
                    spend_date_label(m, doc, active_date_rules, doc_date_candidates),
                    REVIEW_FLAG if not m.spend_date_reviewed else None,
                ]
            )

    revisions_ws = wb.create_sheet("Revisions")
    revisions_ws.append(_REVISIONS_HEADER)
    for doc in result.documents:
        for m in doc.matches:
            for row in _revision_rows(m):
                revisions_ws.append(row)
            for row in _spend_date_revision_rows(m):
                revisions_ws.append(row)

    spend_by_month_ws = wb.create_sheet("Spend By Month")
    spend_by_month_ws.append(_SPEND_BY_MONTH_HEADER)
    for row in _spend_by_month_rows(result):
        spend_by_month_ws.append(row)

    return wb


def save_workbook(wb: Workbook, path: Path) -> None:
    wb.save(path)
