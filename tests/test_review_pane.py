"""The review pane: look at the pixels, fix the number.

Driven through the App's state methods rather than simulated clicks, the
way the existing GUI tests work.
"""

import io
import tkinter as tk
from decimal import Decimal

import pytest
from PIL import Image

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


def _png(width=90, height=30) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


def _match(value: str, confidence=None, crop=True) -> MatchRecord:
    return MatchRecord(
        display_name="scan.pdf",
        location="page 1",
        raw_text=f"${value}",
        rule_id="standard",
        value=Decimal(value),
        provenance="text" if confidence is None else "ocr",
        confidence=confidence,
        crop_png=_png() if (crop and confidence is not None) else None,
    )


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
    return app.last_result


def test_only_guessed_amounts_are_offered_for_review(app):
    _load(app, [_match("100.00"), _match("340.00", confidence=84.0)])

    reviewable = app.reviewable_matches()

    assert [m.value for m in reviewable] == [Decimal("340.00")]


def test_confidently_read_amounts_are_still_offered(app):
    # The whole finding: 84% confidence was wrong. Confidence cannot decide
    # what is safe to skip, so every guess is offered.
    _load(app, [_match("440.00", confidence=84.0), _match("40.00", confidence=31.0)])

    assert len(app.reviewable_matches()) == 2


def test_the_doubtful_ones_come_first(app):
    # Worst-scoring first, so the riskiest gets looked at even if the user
    # stops halfway.
    _load(app, [_match("440.00", confidence=84.0), _match("40.00", confidence=31.0)])

    assert [m.confidence for m in app.reviewable_matches()] == [31.0, 84.0]


def test_nothing_to_review_when_everything_came_from_a_text_layer(app):
    _load(app, [_match("100.00")])

    assert app.reviewable_matches() == []
    assert app.can_review() is False


def test_a_correction_is_parsed_and_applied(app):
    m = _match("440.00", confidence=84.0)
    _load(app, [m])

    error = app.apply_correction(m, "940.00")

    assert error is None
    assert m.effective_value == Decimal("940.00")


def test_a_correction_accepts_the_way_people_actually_type_money(app):
    m = _match("440.00", confidence=84.0)
    _load(app, [m])

    assert app.apply_correction(m, "$1,240.50") is None
    assert m.effective_value == Decimal("1240.50")


def test_a_correction_in_parentheses_is_negative(app):
    # Matches the accounting rule the app already understands.
    m = _match("440.00", confidence=84.0)
    _load(app, [m])

    assert app.apply_correction(m, "($200.00)") is None
    assert m.effective_value == Decimal("-200.00")


def test_an_unparseable_correction_is_rejected_without_changing_anything(app):
    m = _match("440.00", confidence=84.0)
    _load(app, [m])

    error = app.apply_correction(m, "nine hundred")

    assert error
    assert m.corrected_value is None
    assert m.effective_value == Decimal("440.00")


def test_an_empty_correction_is_rejected(app):
    m = _match("440.00", confidence=84.0)
    _load(app, [m])

    assert app.apply_correction(m, "   ")
    assert m.corrected_value is None


def test_accepting_a_reading_marks_it_reviewed_without_changing_the_value(app):
    m = _match("340.00", confidence=84.0)
    _load(app, [m])

    app.accept_reading(m)

    assert m.reviewed is True
    assert m.effective_value == Decimal("340.00")
    assert m.needs_review is False


def test_a_reviewed_amount_drops_out_of_the_queue(app):
    a = _match("440.00", confidence=84.0)
    b = _match("40.00", confidence=31.0)
    _load(app, [a, b])

    app.accept_reading(b)

    assert [m.value for m in app.reviewable_matches(pending_only=True)] == [
        Decimal("440.00")
    ]


def test_corrections_reach_the_preview_total(app):
    m = _match("440.00", confidence=84.0)
    _load(app, [m])

    app.apply_correction(m, "940.00")
    app._refresh_preview_widget()

    rows = [
        tuple(app._preview_tree.item(i)["values"])
        for i in app._preview_tree.get_children()
    ]
    assert any("940.00" in str(r) for r in rows)


def test_the_review_window_opens_and_shows_the_crop(app):
    _load(app, [_match("340.00", confidence=84.0)])

    window = app.open_review_window()

    assert window.winfo_exists()
    assert app.current_review_match().value == Decimal("340.00")


def test_opening_review_twice_reuses_the_same_window(app):
    _load(app, [_match("340.00", confidence=84.0)])

    first = app.open_review_window()
    second = app.open_review_window()

    assert first is second


def test_moving_through_the_queue_changes_the_shown_match(app):
    _load(app, [_match("440.00", confidence=84.0), _match("40.00", confidence=31.0)])
    app.open_review_window()

    first = app.current_review_match()
    app.next_review()

    assert app.current_review_match() is not first


def test_the_queue_does_not_run_off_the_end(app):
    _load(app, [_match("340.00", confidence=84.0)])
    app.open_review_window()

    app.next_review()
    app.next_review()

    assert app.current_review_match() is not None


def test_a_guess_with_no_crop_is_still_reviewable(app):
    # A crop can fail to be taken (unreadable source, render error). The
    # amount still needs a human decision; it just can't be shown.
    m = _match("340.00", confidence=84.0, crop=False)
    _load(app, [m])

    assert app.reviewable_matches() == [m]
    app.open_review_window()
    assert app.current_review_match() is m


def test_the_review_button_is_off_until_something_was_guessed(app):
    assert "disabled" in app._review_button.state()

    _load(app, [_match("100.00")])
    app._refresh_preview_widget()

    assert "disabled" in app._review_button.state()


def test_the_review_button_counts_what_is_still_pending(app):
    _load(app, [_match("440.00", confidence=84.0), _match("40.00", confidence=31.0)])
    app._refresh_preview_widget()

    assert "disabled" not in app._review_button.state()
    assert "(2)" in app._review_button.cget("text")


def test_the_pending_count_drops_as_amounts_are_checked(app):
    a = _match("440.00", confidence=84.0)
    _load(app, [a, _match("40.00", confidence=31.0)])
    app._refresh_preview_widget()

    app.accept_reading(a)

    assert "(1)" in app._review_button.cget("text")


# ---- the optional handwriting model, as a second opinion ----


def _fake_backend(monkeypatch, reading, available=True):
    from cost_extractor import gui as gui_module

    monkeypatch.setattr(gui_module.handwriting, "is_available", lambda: available)
    monkeypatch.setattr(gui_module.handwriting, "read_line", lambda img: reading)


def test_no_second_opinion_when_no_model_is_installed(app, monkeypatch):
    # The default in every packaged build.
    _fake_backend(monkeypatch, None, available=False)
    m = _match("440.00", confidence=82.0)
    _load(app, [m])

    assert app.second_opinion(m) is None


def test_the_model_supplies_a_second_reading_of_the_crop(app, monkeypatch):
    _fake_backend(monkeypatch, "$940.00")
    m = _match("440.00", confidence=82.0)
    _load(app, [m])

    assert app.second_opinion(m) == "$940.00"


def test_a_second_opinion_needs_a_crop_to_read(app, monkeypatch):
    _fake_backend(monkeypatch, "$940.00")
    m = _match("440.00", confidence=82.0, crop=False)
    _load(app, [m])

    assert app.second_opinion(m) is None


def test_a_disagreement_is_surfaced(app, monkeypatch):
    _fake_backend(monkeypatch, "$940.00")
    m = _match("440.00", confidence=82.0)
    _load(app, [m])

    assert app.second_opinion_disagrees(m) is True


def test_agreement_between_the_engines_is_not_flagged(app, monkeypatch):
    _fake_backend(monkeypatch, "S 440. 00")
    m = _match("440.00", confidence=82.0)
    _load(app, [m])

    assert app.second_opinion_disagrees(m) is False


def test_the_second_opinion_never_becomes_a_value_by_itself(app, monkeypatch):
    # The whole safety property: 5/20 accuracy is harmless as a suggestion
    # and catastrophic as an answer.
    _fake_backend(monkeypatch, "$940.00")
    m = _match("440.00", confidence=82.0)
    _load(app, [m])

    app.open_review_window()

    assert m.corrected_value is None
    assert m.effective_value == Decimal("440.00")


def test_taking_the_second_opinion_records_it_as_a_human_decision(app, monkeypatch):
    _fake_backend(monkeypatch, "$940.00")
    m = _match("440.00", confidence=82.0)
    _load(app, [m])
    app.open_review_window()

    assert app.use_second_opinion(m) is None

    assert m.effective_value == Decimal("940.00")
    assert m.reviewed is True


def test_an_unparseable_second_opinion_cannot_be_taken(app, monkeypatch):
    # TrOCR emits things like "Totalsboro ." on a bad read.
    _fake_backend(monkeypatch, "Totalsboro .")
    m = _match("440.00", confidence=82.0)
    _load(app, [m])

    assert app.use_second_opinion(m)
    assert m.corrected_value is None


def test_a_crash_in_the_model_does_not_take_out_the_review_pane(app, monkeypatch):
    from cost_extractor import gui as gui_module

    def boom(_img):
        raise RuntimeError("onnxruntime exploded")

    monkeypatch.setattr(gui_module.handwriting, "is_available", lambda: True)
    monkeypatch.setattr(gui_module.handwriting, "read_line", boom)
    m = _match("440.00", confidence=82.0)
    _load(app, [m])

    assert app.second_opinion(m) is None
    window = app.open_review_window()
    assert window.winfo_exists()
