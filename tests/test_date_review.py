"""Suggesting and confirming a spend date, and its Date Formats rules."""

import tkinter as tk
from datetime import date
from decimal import Decimal

import pytest

from cost_extractor.extractors.base import Status
from cost_extractor.gui import App
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


def _match(raw_text="$100.00", value="100.00", doc_offset=0) -> MatchRecord:
    return MatchRecord(
        display_name="scan.pdf",
        location="page 1",
        raw_text=raw_text,
        rule_id="standard",
        value=Decimal(value),
        doc_offset=doc_offset,
    )


def _load(app, matches, full_text=""):
    """Loads a result the way a real run does, INCLUDING the
    match -> document map _run_worker builds -- a test that skips this
    step (by setting app.last_result directly) leaves _document_for with
    nothing to look up."""
    doc = DocumentResult(
        display_name="scan.pdf",
        status=Status.OK,
        matches=matches,
        subtotal=sum((m.value for m in matches), Decimal("0")),
        full_text=full_text,
    )
    app.last_result = PipelineResult.from_documents([doc])
    app._match_documents = {id(m): doc for m in matches}
    return app.last_result


def test_document_for_finds_the_owning_document(app):
    m = _match()
    result = _load(app, [m])

    assert app._document_for(m) is result.documents[0]


def test_suggest_spend_date_finds_the_nearest_date_in_the_document(app):
    full_text = "Invoice dated 06/14/2026.\n\nAmount: $100.00 due."
    m = _match(doc_offset=full_text.index("$100.00"))
    _load(app, [m], full_text=full_text)

    assert app.suggest_spend_date(m) == date(2026, 6, 14)


def test_suggest_spend_date_returns_none_with_no_dates_in_the_document(app):
    full_text = "No dates anywhere in this text, just $100.00."
    m = _match(doc_offset=full_text.index("$100.00"))
    _load(app, [m], full_text=full_text)

    assert app.suggest_spend_date(m) is None


def test_suggest_spend_date_is_cached(app):
    full_text = "Dated 06/14/2026, amount $100.00."
    m = _match(doc_offset=full_text.index("$100.00"))
    _load(app, [m], full_text=full_text)
    first = app.suggest_spend_date(m)

    # Swap the document out from under the cache -- if suggest_spend_date
    # recomputed instead of using the cache, this would change the answer.
    app._match_documents[id(m)] = DocumentResult(
        display_name="scan.pdf", status=Status.OK, matches=[m], full_text="nothing here"
    )

    assert app.suggest_spend_date(m) == first


def test_confirm_spend_date_records_a_parsed_date(app):
    m = _match()
    _load(app, [m])

    error = app.confirm_spend_date(m, "06/14/2026")

    assert error is None
    assert m.effective_spend_date == date(2026, 6, 14)


def test_a_second_spend_date_confirmation_preserves_the_first_as_history(app):
    m = _match()
    _load(app, [m])

    app.confirm_spend_date(m, "06/01/2026")
    app.confirm_spend_date(m, "06/14/2026", note="fixed typo")

    assert [r.value for r in m.spend_date_revisions] == [
        date(2026, 6, 1),
        date(2026, 6, 14),
    ]
    assert m.spend_date_revisions[-1].note == "fixed typo"
    assert m.effective_spend_date == date(2026, 6, 14)


def test_confirm_spend_date_rejects_unparseable_text(app):
    m = _match()
    _load(app, [m])

    error = app.confirm_spend_date(m, "not a date")

    assert error is not None
    assert m.spend_date_revisions == []


def test_accept_date_suggestion_confirms_the_suggested_date(app):
    full_text = "Dated 06/14/2026, amount $100.00."
    m = _match(doc_offset=full_text.index("$100.00"))
    _load(app, [m], full_text=full_text)

    error = app.accept_date_suggestion(m)

    assert error is None
    assert m.effective_spend_date == date(2026, 6, 14)


def test_accept_date_suggestion_with_no_suggestion_available_is_rejected(app):
    full_text = "No dates here, just $100.00."
    m = _match(doc_offset=full_text.index("$100.00"))
    _load(app, [m], full_text=full_text)

    error = app.accept_date_suggestion(m)

    assert error is not None
    assert m.spend_date_revisions == []


def test_confirm_no_date_records_a_none_valued_revision(app):
    m = _match()
    _load(app, [m])

    app.confirm_no_date(m)

    assert m.spend_date_reviewed is True
    assert m.effective_spend_date is None


def test_confirm_no_date_default_note(app):
    m = _match()
    _load(app, [m])

    app.confirm_no_date(m)

    assert m.spend_date_revisions[-1].note == "confirmed no associated date"
