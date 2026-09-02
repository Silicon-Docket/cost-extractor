import time
import tkinter as tk
from decimal import Decimal
from tkinter import ttk

import pytest

from cost_extractor.gui import App, create_root
from cost_extractor.money_parser import (
    CUSTOM_PATTERN_EXAMPLE_LABEL,
    CUSTOM_PATTERN_EXAMPLE_PATTERN,
    CUSTOM_PATTERN_HELP,
)


@pytest.fixture
def app():
    try:
        root = tk.Tk()
    except tk.TclError as e:
        # Some CI Python distributions (observed: GitHub Actions'
        # actions/setup-python cache for Windows) ship without a usable
        # Tcl/Tk data directory at all, so even a headless Tk() fails at
        # the interpreter level — not a display problem, a packaging one.
        # This is an environment gap, not an app regression; skip rather
        # than fail the build over a toolchain issue outside this repo.
        pytest.skip(f"Tk unavailable in this environment: {e}")
    root.withdraw()
    application = App(root)
    yield application
    root.destroy()


def test_app_starts_with_all_builtin_rules_enabled(app):
    assert len(app.rules) == 3
    assert all(r.enabled for r in app.rules)
    assert all(r.built_in for r in app.rules)


def test_can_run_false_with_no_paths(app):
    assert app.can_run() is False


def test_can_run_false_when_all_rules_disabled(app, tmp_path):
    app.add_paths([tmp_path / "a.docx"])
    for r in app.rules:
        app.toggle_rule(r.id, False)

    assert app.can_run() is False


def test_can_run_true_with_paths_and_a_rule_enabled(app, tmp_path):
    app.add_paths([tmp_path / "a.docx"])

    assert app.can_run() is True


def test_add_paths_deduplicates(app, tmp_path):
    p = tmp_path / "a.docx"
    app.add_paths([p, p])

    assert app.selected_paths == [p]


def test_remove_path_removes_it(app, tmp_path):
    p = tmp_path / "a.docx"
    app.add_paths([p])
    app.remove_path(p)

    assert app.selected_paths == []


def test_add_custom_pattern_success_adds_rule(app):
    error = app.add_custom_pattern(r"(?P<amount>\d+)\s?EUR", "Euro")

    assert error is None
    assert any(r.label == "Euro" and not r.built_in for r in app.rules)


def test_add_custom_pattern_invalid_regex_returns_error_and_does_not_add(app):
    before = len(app.rules)

    error = app.add_custom_pattern(r"(?P<amount>\d+", "Broken")

    assert error is not None
    assert "Invalid regex" in error
    assert len(app.rules) == before


def test_remove_custom_rule_removes_it(app):
    app.add_custom_pattern(r"(?P<amount>\d+)\s?EUR", "Euro")
    custom_id = next(r.id for r in app.rules if not r.built_in)

    app.remove_custom_rule(custom_id)

    assert all(r.built_in for r in app.rules)


def test_snapshot_active_rules_is_unaffected_by_later_toggles(app, tmp_path):
    app.add_paths([tmp_path / "a.docx"])
    snapshot = app._snapshot_active_rules()
    assert len(snapshot) == 3

    for r in app.rules:
        app.toggle_rule(r.id, False)

    assert all(r.enabled for r in snapshot)
    assert not any(r.enabled for r in app.rules)


def test_export_report_without_a_run_returns_error(app, tmp_path):
    error = app.export_report(tmp_path / "out.xlsx")

    assert error is not None
    assert "run" in error.lower()


def test_create_root_does_not_raise():
    try:
        root = create_root()
    except tk.TclError as e:
        pytest.skip(f"Tk unavailable in this environment: {e}")
    try:
        root.withdraw()
        assert isinstance(root, tk.Tk)
    finally:
        root.destroy()


def test_full_app_run_and_export_wiring(app, tmp_path, simple_docx):
    app.add_paths([simple_docx])
    assert app.can_run() is True

    app.start_run()
    deadline = time.time() + 10
    while app.last_result is None and time.time() < deadline:
        app.root.update()
        time.sleep(0.05)

    assert app.last_result is not None
    assert app.last_result.grand_total == Decimal("1734.56")

    out_path = tmp_path / "report.xlsx"
    error = app.export_report(out_path)
    assert error is None
    assert out_path.exists()

    import openpyxl

    wb = openpyxl.load_workbook(out_path, data_only=True)
    summary = wb["Summary"]
    grand_total_row = list(summary.iter_rows(values_only=True))[-1]
    assert grand_total_row[3] == 1734.56


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def test_money_formats_panel_shows_inline_hint_about_amount_group(app):
    hints = [
        w.cget("text")
        for w in _descendants(app.root)
        if isinstance(w, ttk.Label) and "(?P<amount>" in str(w.cget("text"))
    ]

    assert hints, "expected an always-visible hint mentioning (?P<amount>...)"


def test_show_custom_pattern_help_opens_window_containing_help_text(app):
    window = app.show_custom_pattern_help()

    assert isinstance(window, tk.Toplevel)
    assert window.winfo_exists()
    texts = [w for w in _descendants(window) if isinstance(w, tk.Text)]
    assert texts
    assert CUSTOM_PATTERN_HELP.strip() in texts[0].get("1.0", tk.END)


def test_show_custom_pattern_help_twice_reuses_the_same_window(app):
    first = app.show_custom_pattern_help()
    second = app.show_custom_pattern_help()

    assert first is second
    toplevels = [w for w in _descendants(app.root) if isinstance(w, tk.Toplevel)]
    assert len(toplevels) == 1


def test_help_window_has_use_example_button(app):
    window = app.show_custom_pattern_help()

    buttons = [
        w
        for w in _descendants(window)
        if isinstance(w, ttk.Button) and "example" in str(w.cget("text")).lower()
    ]
    assert len(buttons) == 1


def test_use_example_pattern_fills_entries_and_adds_cleanly(app):
    app.use_example_pattern()

    assert app._custom_pattern_entry.get() == CUSTOM_PATTERN_EXAMPLE_PATTERN
    assert app._custom_label_entry.get() == CUSTOM_PATTERN_EXAMPLE_LABEL

    app._on_add_custom_pattern()

    assert any(
        r.label == CUSTOM_PATTERN_EXAMPLE_LABEL and not r.built_in for r in app.rules
    )
    assert app._custom_pattern_entry.get() == ""
    assert app._rule_error_label.cget("text") == ""
