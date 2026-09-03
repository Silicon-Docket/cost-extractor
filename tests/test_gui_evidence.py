"""The app itself has to show what it guessed, not just the spreadsheet."""

import tkinter as tk
from decimal import Decimal

import pytest

from cost_extractor.extractors.base import Status
from cost_extractor.gui import App
from cost_extractor.ingestion import IMAGE_SUFFIXES
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult


@pytest.fixture
def app():
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk unavailable in this environment: {e}")
    root.withdraw()
    application = App(root)
    yield application
    root.destroy()


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


def _rows(app) -> list[tuple]:
    tree = app._preview_tree
    return [tuple(tree.item(i)["values"]) for i in tree.get_children()]


def _load(app, matches):
    app.last_result = PipelineResult.from_documents(
        [
            DocumentResult(
                display_name="scan.pdf",
                status=Status.OK,
                matches=matches,
                subtotal=sum((m.value for m in matches), Decimal("0")),
            )
        ]
    )
    app._refresh_preview_widget()


def test_preview_marks_a_doubtful_amount_for_review(app):
    _load(app, [_match("200.00", confidence=95.0), _match("40.00", confidence=31.0)])

    flags = [row[-1] for row in _rows(app)]
    assert "REVIEW" in flags


def test_preview_does_not_flag_a_confidently_read_amount(app):
    _load(app, [_match("200.00", confidence=95.0)])

    assert _rows(app)[0][-1] == ""


def test_preview_shows_the_confidence_of_a_guessed_amount(app):
    _load(app, [_match("200.00", confidence=95.0)])

    assert "95" in str(_rows(app)[0])


def test_preview_leaves_confidence_blank_for_directly_read_text(app):
    # A blank cell says "nothing was guessed"; a 0% would say "read badly".
    _load(app, [_match("100.00")])

    confidence_cell = _rows(app)[0][-2]
    assert confidence_cell == ""


def test_preview_reports_how_much_of_the_total_needs_review(app):
    _load(app, [_match("200.00", confidence=95.0), _match("40.00", confidence=31.0)])

    text = " ".join(str(row) for row in _rows(app))
    assert "240.00" in text  # the combined total is still the headline
    assert "40.00" in text  # and the shaky part is called out


def test_the_file_picker_offers_scan_formats(app):
    # Drag-and-drop already accepts anything; the dialog filter was the
    # only place still implying images were not supported.
    patterns = app.file_dialog_patterns()

    for suffix in IMAGE_SUFFIXES:
        assert f"*{suffix}" in patterns
