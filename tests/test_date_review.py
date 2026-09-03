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


def test_add_date_rule_success_adds_rule(app):
    error = app.add_date_rule(r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})", "ISO")

    assert error is None
    assert any(r.label == "ISO" and not r.built_in for r in app.date_rules)


def test_add_date_rule_invalid_pattern_returns_error_and_does_not_add(app):
    before = len(app.date_rules)

    error = app.add_date_rule(r"(?P<year>\d{4}", "Broken")

    assert error is not None
    assert len(app.date_rules) == before


def test_remove_date_rule_removes_a_custom_rule(app):
    app.add_date_rule(r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})", "ISO")
    custom_id = next(r.id for r in app.date_rules if not r.built_in)

    app.remove_date_rule(custom_id)

    assert all(r.built_in for r in app.date_rules)


def test_remove_date_rule_refuses_to_remove_a_built_in_rule(app):
    built_in_id = next(r.id for r in app.date_rules if r.built_in)
    before = len(app.date_rules)

    app.remove_date_rule(built_in_id)

    assert len(app.date_rules) == before


def test_toggle_date_rule_disables_it(app):
    rule_id = app.date_rules[0].id

    app.toggle_date_rule(rule_id, False)

    assert app.date_rules[0].enabled is False


def test_adding_a_date_rule_invalidates_the_suggestion_cache(app):
    full_text = "Reference 2026-06-14, amount $100.00."
    m = _match(doc_offset=full_text.index("$100.00"))
    _load(app, [m], full_text=full_text)

    # Seed the cache BEFORE the matching rule exists, and assert it
    # seeded None -- otherwise a passing test below wouldn't prove the
    # cache was actually cleared rather than never populated.
    assert app.suggest_spend_date(m) is None

    app.add_date_rule(r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})", "ISO")

    assert app.suggest_spend_date(m) == date(2026, 6, 14)


def test_removing_a_date_rule_invalidates_the_suggestion_cache(app):
    app.add_date_rule(r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})", "ISO")
    custom_id = next(r.id for r in app.date_rules if not r.built_in)
    full_text = "Reference 2026-06-14, amount $100.00."
    m = _match(doc_offset=full_text.index("$100.00"))
    _load(app, [m], full_text=full_text)
    assert app.suggest_spend_date(m) == date(2026, 6, 14)

    app.remove_date_rule(custom_id)

    assert app.suggest_spend_date(m) is None


def test_toggling_a_date_rule_off_invalidates_the_suggestion_cache(app):
    full_text = "Dated 06/14/2026, amount $100.00."
    m = _match(doc_offset=full_text.index("$100.00"))
    _load(app, [m], full_text=full_text)
    assert app.suggest_spend_date(m) == date(2026, 6, 14)
    builtin_id = app.date_rules[0].id

    app.toggle_date_rule(builtin_id, False)

    assert app.suggest_spend_date(m) is None
