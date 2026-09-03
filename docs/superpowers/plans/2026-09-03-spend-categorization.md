# Spend Categorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let every extracted amount be assigned a human-confirmed spend category, and export a per-category breakdown of confirmed and rule-suggested-but-unconfirmed totals.

**Architecture:** A new `category_rules.py` module (presence-detection rule engine, mirroring `money_parser.py`'s shape but simpler — no value extraction, no parser) suggests a category from the line of text an amount was found on; `pipeline.py` captures that line at extraction time; `gui.py` adds a suggest/confirm workflow (mirroring the existing OCR review pane) plus a rule-management panel; `report.py` exports the confirmed/suggested state per match and a new per-category summary sheet.

**Tech Stack:** Python 3, Tkinter (stdlib), `re`, `dataclasses`, `typing.Optional`, openpyxl. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-03-spend-categorization-design.md`

## Global Constraints

- Every match needs a confirmed category to count in a category total — no toggle between confirmed-only and suggestion-included totals; both are always shown side by side (mirrors the existing `confident_total`/`review_total`/`grand_total` pattern).
- Category rules match against the same line of text as the amount — not the whole segment, not a character window.
- Category suggestions are computed on demand from `MatchRecord.line_text` + the live rule set — never stored eagerly, never cached across a rule-set change without invalidation. Learned the hard way on the sibling spend-over-time branch: the suggestion cache must ALSO be cleared on every new pipeline run, not just on rule changes — a stale `id(match)`-keyed cache entry from a previous run can silently survive into a new run if Python reuses a freed object's address, attributing a wrong suggestion to an unrelated match. This plan's Task 3 builds that invalidation in from the start rather than leaving it to be caught later.
- A separate review window and rules panel from the OCR review pane — categorization queues every match, not just OCR-guessed ones, and is a different kind of decision.
- One confirmed category per match — no multi-category assignment.
- No auto-committing a rule-suggested category to a total without confirmation. No recording *who* categorized something, or full UI-interaction logging.
- `build_workbook`'s existing single-argument call sites must keep compiling and behaving unchanged (the new `category_rules` parameter is optional, defaulting to `None`/`[]`).
- This branch (`spend-categorization`) is forked from `main` (after PR #1 merged) and does not contain the sibling `spend-over-time` branch's date machinery — do not reference `date_rules`, `DateRule`, `spend_date_revisions`, or a `Dimension` column value other than `"Value"`/`"Category"` anywhere in this plan's code. This plan introduces the Revisions sheet's `Dimension` column fresh, from this branch's own perspective — see the spec's Rollout section for how a later merge-order conflict with `spend-over-time` gets resolved (not this plan's concern).

---

## File Structure

- `cost_extractor/category_rules.py` (new) — the category rule engine: `CategoryRule`, `default_rules`, `suggest_category`, `build_custom_rule`.
- `cost_extractor/pipeline.py` (modify) — `MatchRecord.line_text`/`category_revisions`/`category_reviewed`/`effective_category`, `_line_containing` helper, the `_process_single_file` loop wiring, `PipelineResult.uncategorized_count`.
- `cost_extractor/gui.py` (modify) — `App` state, suggest/confirm methods, rule-management methods, the "Categories" panel and "Categorize Amounts…" window widgets, README documentation for the new feature.
- `cost_extractor/report.py` (modify) — `build_workbook`'s new parameter, Details sheet columns, Summary row, Revisions sheet `Dimension` column, new "Categories" sheet.
- `README.md` (modify) — document the new panel/window/report additions, mirroring the existing OCR-review and custom-money-pattern sections' style.
- `tests/test_category_rules.py` (new) — the rule engine, standalone.
- `tests/test_categorization.py` (new) — `pipeline.py`-level: `_line_containing`, `MatchRecord`/`PipelineResult` properties.
- `tests/test_category_review.py` (new) — `gui.py`-level: suggest/confirm, rule management, cache invalidation (including the across-runs case), the window widgets.
- `tests/test_report.py`, `tests/test_report_evidence.py` (modify) — mechanical ripple: sheet name lists, `_REVISIONS_HEADER` assertion.
- `tests/test_report_categorization.py` (new) — `report.py`-level: Details columns, Summary row, Revisions `Dimension` rows, Categories sheet.

---

## Task 1: `category_rules.py` — the category rule engine

**Files:**
- Create: `cost_extractor/category_rules.py`
- Test: `tests/test_category_rules.py`

**Interfaces:**
- Consumes: `cost_extractor.money_parser._is_pattern_too_slow(compiled: re.Pattern) -> bool` (existing, reused unchanged).
- Produces: `CategoryRule` (dataclass: `id: str`, `label: str`, `pattern: str`, `priority: int = 50`, `enabled: bool = True`, `built_in: bool = True`, `flags: int = re.IGNORECASE`, `compiled: re.Pattern` (post-init)). `default_rules() -> list[CategoryRule]`. `suggest_category(line_text: str, rules: list[CategoryRule]) -> Optional[str]`. `build_custom_rule(pattern_str: str, label: Optional[str], index: int) -> CategoryRule` (raises `ValueError` on invalid input).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_category_rules.py`:

```python
import pytest

from cost_extractor.category_rules import build_custom_rule, default_rules, suggest_category


def test_default_rules_returns_fresh_instances_each_call():
    first_call = default_rules()
    for rule in first_call:
        rule.enabled = False

    second_call = default_rules()

    assert all(rule.enabled for rule in second_call)


def test_suggest_category_returns_none_on_no_match():
    assert suggest_category("Nothing relevant on this line.", default_rules()) is None


def test_suggest_category_matches_materials():
    assert suggest_category("2 boxes of materials delivered", default_rules()) == "Materials"


def test_suggest_category_matches_labor():
    assert suggest_category("40 hours of labor billed", default_rules()) == "Labor"


def test_suggest_category_matches_travel():
    assert suggest_category("mileage reimbursement for travel", default_rules()) == "Travel"


def test_suggest_category_matches_fees():
    assert suggest_category("processing fees applied", default_rules()) == "Fees"


def test_suggest_category_is_case_insensitive():
    assert suggest_category("LABOR CHARGES THIS WEEK", default_rules()) == "Labor"


def test_suggest_category_picks_highest_priority_on_multiple_matches():
    # "materials" (priority 0) and "labor" (priority 1) both appear;
    # lower priority number wins, same tie-break as money_parser.
    rules = default_rules()

    result = suggest_category("materials and labor on the same line", rules)

    assert result == "Materials"


def test_suggest_category_ignores_a_disabled_rule():
    rules = default_rules()
    for rule in rules:
        if rule.id == "materials":
            rule.enabled = False

    assert suggest_category("materials delivered", rules) is None


def test_build_custom_rule_matches_and_is_case_insensitive():
    rule = build_custom_rule(r"\bpermits?\b", "Permits", 0)

    assert suggest_category("Building PERMIT fee", [rule]) == "Permits"
    assert rule.enabled is True
    assert rule.built_in is False


def test_build_custom_rule_rejects_invalid_regex():
    with pytest.raises(ValueError, match="Invalid regex"):
        build_custom_rule(r"\bpermits?\b(", "Broken", 0)


def test_build_custom_rule_rejects_catastrophic_backtracking_pattern():
    with pytest.raises(ValueError, match="slow|backtrack"):
        build_custom_rule(r"(a+)+b", "Evil", 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_category_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cost_extractor.category_rules'`

- [ ] **Step 3: Implement `category_rules.py`**

Create `cost_extractor/category_rules.py`:

```python
"""Regex-based rule engine for detecting spend categories in text.

Mirrors cost_extractor/money_parser.py's shape closely -- same
id/label/pattern/priority/enabled/built_in fields as MoneyFormatRule --
but simpler: a category rule's job is presence detection ("does this
line mention Labor"), not value extraction, so there's no normalizer/
parser callback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CategoryRule:
    id: str
    label: str
    pattern: str
    priority: int = 50
    enabled: bool = True
    built_in: bool = True
    flags: int = re.IGNORECASE
    compiled: re.Pattern = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.compiled = re.compile(self.pattern, self.flags)


def default_rules() -> list[CategoryRule]:
    """A small illustrative starter set, not a template meant to fit every
    case -- editable and removable like everything else here. Fresh
    instances every call, matching money_parser.default_rules()'s own
    reasoning: CategoryRule.enabled is mutated in place by the GUI."""
    return [
        CategoryRule(id="materials", label="Materials",
                     pattern=r"\bmaterials?\b|\bsupplies\b", priority=0),
        CategoryRule(id="labor", label="Labor",
                     pattern=r"\blabor\b|\bhours?\b", priority=1),
        CategoryRule(id="travel", label="Travel",
                     pattern=r"\btravel\b|\bmileage\b", priority=2),
        CategoryRule(id="fees", label="Fees",
                     pattern=r"\bfees?\b|\bsurcharge\b", priority=3),
    ]


def suggest_category(line_text: str, rules: list[CategoryRule]) -> Optional[str]:
    """The best category label for one line of text, or None if nothing
    matches. Enabled rules only, lowest `priority` value wins on a tie --
    same conflict-resolution shape as money_parser's rule priority, so a
    line mentioning two category keywords resolves deterministically
    rather than picking whichever the regex engine visits first."""
    candidates = [
        r for r in rules if r.enabled and r.compiled.search(line_text)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda r: r.priority).label


def build_custom_rule(pattern_str: str, label: Optional[str], index: int) -> CategoryRule:
    """Validates and builds a user-supplied category rule. Raises
    ValueError with a user-facing message on invalid regex; never lets
    re.error escape to the GUI. Reuses money_parser's ReDoS probe rather
    than reimplementing it -- same risk, same fix."""
    from cost_extractor.money_parser import _is_pattern_too_slow  # shared guard

    try:
        compiled = re.compile(pattern_str, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"Invalid regex: {e}") from e
    if _is_pattern_too_slow(compiled):
        raise ValueError(
            "Pattern is too slow / potentially catastrophic backtracking; simplify it."
        )
    return CategoryRule(
        id=f"custom_{index}",
        label=label or f"Custom category {index}",
        pattern=pattern_str,
        priority=100 + index,
        enabled=True,
        built_in=False,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_category_rules.py -v`
Expected: PASS (all 12 tests)

- [ ] **Step 5: Commit**

```bash
git add cost_extractor/category_rules.py tests/test_category_rules.py
git commit -m "feat: add category_rules.py, the spend-category presence-detection rule engine"
```

---

## Task 2: `pipeline.py` — capture `line_text`, category revisions

**Files:**
- Modify: `cost_extractor/pipeline.py:1-11` (imports), `:37-86` (`MatchRecord`), `:110-174` (`PipelineResult`), `:216-284` (`_process_single_file`)
- Test: `tests/test_categorization.py` (new)

**Interfaces:**
- Consumes: `cost_extractor.revisions.Revision`, `record_revision`, `latest_value` (existing, unchanged).
- Produces: `_line_containing(text: str, start: int) -> str`. `MatchRecord.line_text: str`, `category_revisions: list[Revision[Optional[str]]]`, `category_reviewed: bool` (property), `effective_category: Optional[str]` (property). `PipelineResult.uncategorized_count: int` (property). Used by Task 3 (`gui.py`) and Tasks 6-7 (`report.py`).

- [ ] **Step 1: Write the failing tests for `_line_containing` and the new properties**

Create `tests/test_categorization.py`:

```python
"""A human-confirmed spend category, and the line-scoped suggestion it's
based on."""

from datetime import datetime, timezone
from decimal import Decimal

from cost_extractor.extractors.base import Status
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult, _line_containing
from cost_extractor.revisions import record_revision

_NOW = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)


def _match(value: str = "100.00") -> MatchRecord:
    return MatchRecord(
        display_name="scan.pdf",
        location="page 1",
        raw_text=f"${value}",
        rule_id="standard",
        value=Decimal(value),
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


def test_line_containing_a_match_on_the_first_line():
    text = "Materials: $100.00\nLabor: $200.00\nTravel: $50.00"

    assert _line_containing(text, text.index("$100.00")) == "Materials: $100.00"


def test_line_containing_a_match_on_a_middle_line():
    text = "Materials: $100.00\nLabor: $200.00\nTravel: $50.00"

    assert _line_containing(text, text.index("$200.00")) == "Labor: $200.00"


def test_line_containing_a_match_on_the_last_line_with_no_trailing_newline():
    text = "Materials: $100.00\nLabor: $200.00\nTravel: $50.00"

    assert _line_containing(text, text.index("$50.00")) == "Travel: $50.00"


def test_line_containing_a_single_line_segment():
    text = "Just one line: $100.00 total"

    assert _line_containing(text, text.index("$100.00")) == text


def test_a_never_reviewed_match_is_not_category_reviewed():
    m = _match()

    assert m.category_reviewed is False
    assert m.effective_category is None


def test_confirming_a_category_marks_it_reviewed():
    m = _match()

    record_revision(m.category_revisions, "Materials", now=_NOW)

    assert m.category_reviewed is True
    assert m.effective_category == "Materials"


def test_a_second_category_confirmation_preserves_the_first_as_history():
    m = _match()
    first = datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc)
    second = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)

    record_revision(m.category_revisions, "Materials", now=first)
    record_revision(m.category_revisions, "Labor", note="fixed", now=second)

    assert [r.value for r in m.category_revisions] == ["Materials", "Labor"]
    assert m.effective_category == "Labor"


def test_uncategorized_count_counts_a_never_reviewed_match():
    result = _result([_match()])

    assert result.uncategorized_count == 1


def test_uncategorized_count_excludes_a_confirmed_category():
    m = _match()
    record_revision(m.category_revisions, "Materials", now=_NOW)
    result = _result([m])

    assert result.uncategorized_count == 0


def test_uncategorized_count_is_independent_of_unreviewed_ocr_count():
    # An OCR-derived match that's category-confirmed but NOT value-reviewed
    # must still count as categorized -- proving uncategorized_count uses
    # its own filter (category_reviewed), not accidentally reusing
    # unreviewed_ocr_count's (value_reviewed + provenance == "ocr").
    m = MatchRecord(
        display_name="scan.pdf",
        location="page 1",
        raw_text="$40.00",
        rule_id="standard",
        value=Decimal("40.00"),
        provenance="ocr",
        confidence=31.0,
    )
    record_revision(m.category_revisions, "Materials", now=_NOW)
    result = _result([m])

    assert result.uncategorized_count == 0
    assert result.unreviewed_ocr_count == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_categorization.py -v`
Expected: FAIL — `ImportError: cannot import name '_line_containing'` and `TypeError`/`AttributeError` on the new fields/properties.

- [ ] **Step 3: Add `_line_containing` and the new `MatchRecord`/`PipelineResult` fields/properties**

In `cost_extractor/pipeline.py`, add near the top (after the existing `_crop_png` function, before `_extract` — i.e. after line 201's closing, since both are extraction-time helper functions):

```python
def _line_containing(text: str, start: int) -> str:
    """The single line of `text` that character offset `start` falls in."""
    line_start = text.rfind("\n", 0, start) + 1  # 0 if no newline found
    line_end = text.find("\n", start)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end]
```

In `MatchRecord` (after the existing `value_needs_review` property, i.e. after line 85's `return self.confidence < LOW_CONFIDENCE_THRESHOLD`), add:

```python
    # The specific text line this amount was found on -- segment.text
    # split on newlines, the line containing this match's character
    # offset. Captured at extraction time because segments are transient
    # (gone once run_pipeline returns); category-rule suggestions need
    # this same line text on demand later, in the GUI, without re-running
    # extraction (which for an OCR'd page would mean re-running OCR).
    line_text: str = ""
    # Every human decision about this amount's category, in order -- same
    # append-only discipline as value_revisions. Typed Optional[str]:
    # "no category yet" is a real, expected state (every match starts
    # uncategorized), unlike a money value, which is never absent.
    category_revisions: list[Revision[Optional[str]]] = field(default_factory=list)

    @property
    def category_reviewed(self) -> bool:
        return bool(self.category_revisions)

    @property
    def effective_category(self) -> Optional[str]:
        """The confirmed category, or None ("Uncategorized") if nobody
        has confirmed one yet. Unlike effective_value, there is no
        machine-extracted fallback -- a category is only ever a
        suggestion until a human confirms it, never an extraction."""
        return latest_value(self.category_revisions, None)
```

In `PipelineResult` (after the existing `unreviewed_ocr_count` property, i.e. after line 173's closing), add:

```python
    @property
    def uncategorized_count(self) -> int:
        """Every match nobody has confirmed a category for yet --
        deliberately every provenance and every suggestion state, not
        just OCR-derived or not-yet-suggested ones: "still needs a
        category assigned" means exactly category_reviewed is False,
        full stop, the same way unreviewed_ocr_count doesn't carve out
        confidently-guessed amounts."""
        return sum(
            1
            for doc in self.documents
            for m in doc.matches
            if not m.category_reviewed
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_categorization.py -v`
Expected: PASS (all 10 tests)

- [ ] **Step 5: Run the full existing suite to confirm nothing else broke**

Run: `.venv/Scripts/python.exe -m pytest tests/test_corrections.py tests/test_pipeline.py tests/test_pipeline_e2e.py tests/test_report.py tests/test_report_evidence.py -v`
Expected: PASS -- both new dataclass fields have defaults (`line_text: str = ""`, `category_revisions: list = field(default_factory=list)`), so every existing `MatchRecord(...)` construction in these files keeps compiling unchanged.

- [ ] **Step 6: Write the failing test for `line_text` capture through the real extraction loop**

Append to `tests/test_categorization.py`:

```python
def test_process_single_file_captures_line_text(monkeypatch):
    from cost_extractor.extractors.base import ExtractionResult, TextSegment
    from cost_extractor.ingestion import DiscoveredFile
    from cost_extractor.money_parser import default_rules
    from cost_extractor import pipeline as pipeline_module

    segment_text = "Materials: $100.00\nLabor: $200.00\nTravel: $50.00"
    segments = [TextSegment(text=segment_text, location="page 1")]
    fake_extraction = ExtractionResult(status=Status.OK, segments=segments)
    monkeypatch.setattr(
        pipeline_module, "_extract", lambda discovered, ocr_enabled: fake_extraction
    )
    discovered = DiscoveredFile(display_name="fake.docx", suffix=".docx", status=None)

    doc = pipeline_module._process_single_file(discovered, default_rules(), ocr_enabled=True)

    by_value = {m.raw_text: m.line_text for m in doc.matches}
    assert by_value["$100.00"] == "Materials: $100.00"
    assert by_value["$200.00"] == "Labor: $200.00"
    assert by_value["$50.00"] == "Travel: $50.00"
```

- [ ] **Step 7: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_categorization.py -v`
Expected: FAIL — every match's `line_text` is `""` (the default), since `_process_single_file` doesn't compute it yet.

- [ ] **Step 8: Wire `_line_containing` into `_process_single_file`'s match-building loop**

In `cost_extractor/pipeline.py`, inside `_process_single_file`'s `for m, evidence in zip(found, evidences):` loop (around line 260-274), add one keyword argument to the existing `MatchRecord(...)` construction:

```python
        for m, evidence in zip(found, evidences):
            matches.append(
                MatchRecord(
                    display_name=discovered.display_name,
                    location=segment.location,
                    raw_text=m.raw_text,
                    rule_id=m.rule_id,
                    value=m.value,
                    provenance=segment.provenance,
                    confidence=evidence.confidence if evidence else None,
                    bbox=evidence.bbox if evidence else None,
                    render_scale=segment.render_scale,
                    crop_png=_crop_png(page, evidence.bbox) if evidence else None,
                    line_text=_line_containing(segment.text, m.start),
                )
            )
```

(The only change from the current code is the added `line_text=_line_containing(segment.text, m.start),` line — nothing else in the loop changes.)

- [ ] **Step 9: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_categorization.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 10: Run the full existing suite to confirm nothing else broke**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add cost_extractor/pipeline.py tests/test_categorization.py
git commit -m "feat: capture line_text per match, category revision fields"
```

---

## Task 3: `gui.py` — App state and the suggest/confirm core

**Files:**
- Modify: `cost_extractor/gui.py:1-53` (imports), `:72-91` (`App.__init__`), `:287-298` (`_run_worker`)
- Test: `tests/test_category_review.py` (new)

**Interfaces:**
- Consumes: `cost_extractor.category_rules.default_rules`, `suggest_category` (Task 1). `MatchRecord.line_text`, `category_revisions`, `category_reviewed`, `effective_category` (Task 2).
- Produces: `App.category_rules: list`, `App.suggest_category(match) -> Optional[str]`, `App.confirm_category(match, category, note=None) -> Optional[str]`, `App.accept_category_suggestion(match, note=None) -> Optional[str]`. Used by Task 4 (rule management) and Task 5 (window widgets).

- [ ] **Step 1: Write the failing tests for the suggest/confirm core**

Create `tests/test_category_review.py`:

```python
"""Suggesting and confirming a spend category, and its Categories rules."""

import tkinter as tk
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


def _match(raw_text="$100.00", value="100.00", line_text="") -> MatchRecord:
    return MatchRecord(
        display_name="scan.pdf",
        location="page 1",
        raw_text=raw_text,
        rule_id="standard",
        value=Decimal(value),
        line_text=line_text,
    )


def _load(app, matches) -> PipelineResult:
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


def test_suggest_category_finds_a_match_from_the_line_text(app):
    m = _match(line_text="materials delivered today")
    _load(app, [m])

    assert app.suggest_category(m) == "Materials"


def test_suggest_category_returns_none_with_no_match_on_the_line(app):
    m = _match(line_text="nothing relevant here")
    _load(app, [m])

    assert app.suggest_category(m) is None


def test_suggest_category_is_cached(app):
    m = _match(line_text="materials delivered")
    _load(app, [m])
    first = app.suggest_category(m)

    # Mutate the match's own line_text -- if suggest_category recomputed
    # instead of using the cache, this would change the answer.
    m.line_text = "nothing relevant now"

    assert app.suggest_category(m) == first


def test_confirm_category_records_a_category(app):
    m = _match()
    _load(app, [m])

    error = app.confirm_category(m, "Materials")

    assert error is None
    assert m.effective_category == "Materials"


def test_confirm_category_rejects_an_empty_category(app):
    m = _match()
    _load(app, [m])

    error = app.confirm_category(m, "   ")

    assert error is not None
    assert m.category_revisions == []


def test_a_second_category_confirmation_preserves_the_first_as_history(app):
    m = _match()
    _load(app, [m])

    app.confirm_category(m, "Materials")
    app.confirm_category(m, "Labor", note="reclassified")

    assert [r.value for r in m.category_revisions] == ["Materials", "Labor"]
    assert m.category_revisions[-1].note == "reclassified"
    assert m.effective_category == "Labor"


def test_accept_category_suggestion_confirms_the_suggested_category(app):
    m = _match(line_text="materials delivered today")
    _load(app, [m])

    error = app.accept_category_suggestion(m)

    assert error is None
    assert m.effective_category == "Materials"


def test_accept_category_suggestion_with_no_suggestion_available_is_rejected(app):
    m = _match(line_text="nothing relevant here")
    _load(app, [m])

    error = app.accept_category_suggestion(m)

    assert error is not None
    assert m.category_revisions == []


def test_category_suggestions_cache_is_cleared_between_runs(app, monkeypatch):
    # The exact bug caught late on the sibling spend-over-time branch:
    # a stale id(match)-keyed cache entry from a PRIOR run must not
    # survive into a NEW run's different MatchRecord. Drives the real
    # _run_worker path (not the _load test shortcut) for the second run,
    # so this test actually exercises the cache-clear this task adds.
    m1 = _match(line_text="materials delivered")
    _load(app, [m1])
    assert app.suggest_category(m1) == "Materials"  # seed the cache

    m2 = _match(raw_text="$200.00", value="200.00", line_text="nothing relevant here")
    doc2 = DocumentResult(
        display_name="scan2.pdf", status=Status.OK, matches=[m2], subtotal=Decimal("200.00"),
    )
    result2 = PipelineResult.from_documents([doc2])

    import cost_extractor.gui as gui_module

    monkeypatch.setattr(gui_module, "run_pipeline", lambda *a, **k: result2)
    app._run_worker([], [])

    assert app.suggest_category(m2) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_category_review.py -v`
Expected: FAIL — `AttributeError: 'App' object has no attribute 'suggest_category'` (and similarly for the other new methods). Note: `App.suggest_category` (the method) will collide with the free function `category_rules.suggest_category` in name only, not in Python scope — see Step 3's import note.

- [ ] **Step 3: Add the `category_rules` import and new `App.__init__` state**

In `cost_extractor/gui.py`, add to the imports block (after the existing `from cost_extractor import handwriting` on line 30):

```python
from cost_extractor import category_rules
```

Module-qualified, not `from cost_extractor.category_rules import suggest_category` — matches this file's existing convention for this kind of on-demand-computation module (`from cost_extractor import handwriting`), and avoids a real ambiguity: the method being defined below is also called `suggest_category`, so an unqualified import of the free function would shadow it inside the class body's own method resolution for anyone reading casually, and would make `monkeypatch` targets ambiguous.

In `App.__init__` (after the existing `self._second_opinions: dict[int, Optional[str]] = {}` on line 87, before `self._build_widgets()`), add:

```python
        self.category_rules: list[category_rules.CategoryRule] = category_rules.default_rules()
        # Same id(match)-keyed cache shape as _second_opinions -- must be
        # invalidated whenever self.category_rules changes (Task 4) AND
        # on every new pipeline run (Step 5 below) -- a stale entry from
        # a previous run's freed MatchRecord could otherwise be returned
        # for an unrelated match in a new run that happens to land at
        # the same id().
        self._category_suggestions: dict[int, Optional[str]] = {}
        self._custom_category_rule_count = 0
        self._category_window: Optional[tk.Toplevel] = None
        self.category_review_index = 0
```

- [ ] **Step 4: Run the tests to verify the state-only tests still fail correctly**

Run: `.venv/Scripts/python.exe -m pytest tests/test_category_review.py -v`
Expected: FAIL — still `AttributeError` on `suggest_category`/`confirm_category`/etc., since only state exists so far, not the methods that use it.

- [ ] **Step 5: Clear the suggestion cache on every new run**

Replace `_run_worker` (`cost_extractor/gui.py:287-298`) with:

```python
    def _run_worker(
        self, paths: list[Path], active_rules: list[MoneyFormatRule]
    ) -> None:
        result = run_pipeline(
            paths,
            active_rules,
            ocr_enabled=True,
            progress_cb=lambda name: self._progress_queue.put(f"progress:{name}"),
            cancel_flag=self.cancel_flag,
        )
        self._category_suggestions.clear()
        self.last_result = result
        self._progress_queue.put("done")
```

(The only changes from the current code: the new `self._category_suggestions.clear()` line, placed BEFORE `self.last_result = result` is assigned -- so any UI gated on `last_result` never sees a result whose cache hasn't already been cleared.)

- [ ] **Step 6: Implement `suggest_category`, `confirm_category`, `accept_category_suggestion`, and the refresh stub they call**

Add these methods to `App`, placed after `use_second_opinion` (`cost_extractor/gui.py`, after line 238's `return self.apply_correction(match, reading, note=combined_note)`, before `current_review_match`):

```python
    def suggest_category(self, match: MatchRecord) -> Optional[str]:
        """The best category for this match's own line, computed on
        demand and cached per match. Recomputed only when the cache is
        explicitly invalidated (rule changes -- Task 4 -- or a new run --
        Step 5 above), never on a timer or a document reload."""
        cached = self._category_suggestions.get(id(match), _UNREAD)
        if cached is not _UNREAD:
            return cached
        suggestion = category_rules.suggest_category(match.line_text, self.category_rules)
        self._category_suggestions[id(match)] = suggestion
        return suggestion

    def confirm_category(
        self, match: MatchRecord, category: str, note: Optional[str] = None
    ) -> Optional[str]:
        cleaned = category.strip()
        if not cleaned:
            return "Enter a category"
        record_revision(match.category_revisions, cleaned, note=note)
        self._after_category_change()
        return None

    def accept_category_suggestion(
        self, match: MatchRecord, note: Optional[str] = None
    ) -> Optional[str]:
        suggestion = self.suggest_category(match)
        if suggestion is None:
            return "No category suggestion available for this line."
        cleaned = (note or "").strip() or None
        record_revision(match.category_revisions, suggestion, note=cleaned or "confirmed")
        self._after_category_change()
        return None

    def _after_category_change(self) -> None:
        self._refresh_category_widgets()

    def _refresh_category_widgets(self) -> None:
        # Guards exactly like _refresh_review_widgets: safe to call even
        # when the "Categorize Amounts..." window (Task 5) isn't open.
        # Task 5 extends this method's body once the window exists to
        # refresh; it does not replace this guard.
        window = self._category_window
        if window is None or not window.winfo_exists():
            return
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_category_review.py -v`
Expected: PASS (all 9 tests)

- [ ] **Step 8: Run the full existing suite to confirm nothing else broke**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add cost_extractor/gui.py tests/test_category_review.py
git commit -m "feat: add App.suggest_category/confirm_category/accept_category_suggestion"
```

---

## Task 4: `gui.py` — Categories rule management

**Files:**
- Modify: `cost_extractor/gui.py` (add methods to `App`, after Task 3's additions)
- Test: `tests/test_category_review.py` (append)

**Interfaces:**
- Consumes: `App.category_rules`, `App._category_suggestions`, `App._custom_category_rule_count` (Task 3 state). `category_rules.build_custom_rule` (Task 1).
- Produces: `App.add_category_rule(pattern_str, label) -> Optional[str]`, `App.remove_category_rule(rule_id) -> None`, `App.toggle_category_rule(rule_id, enabled) -> None`. Used by Task 5 (widget event handlers).

- [ ] **Step 1: Write the failing tests for rule management and cache invalidation**

Append to `tests/test_category_review.py`:

```python
def test_add_category_rule_success_adds_rule(app):
    error = app.add_category_rule(r"\bpermits?\b", "Permits")

    assert error is None
    assert any(r.label == "Permits" and not r.built_in for r in app.category_rules)


def test_add_category_rule_invalid_pattern_returns_error_and_does_not_add(app):
    before = len(app.category_rules)

    error = app.add_category_rule(r"\bpermits?\b(", "Broken")

    assert error is not None
    assert len(app.category_rules) == before


def test_remove_category_rule_removes_a_custom_rule(app):
    app.add_category_rule(r"\bpermits?\b", "Permits")
    custom_id = next(r.id for r in app.category_rules if not r.built_in)

    app.remove_category_rule(custom_id)

    assert all(r.built_in for r in app.category_rules)


def test_toggle_category_rule_disables_it(app):
    rule_id = app.category_rules[0].id

    app.toggle_category_rule(rule_id, False)

    assert app.category_rules[0].enabled is False


def test_adding_a_category_rule_invalidates_the_suggestion_cache(app):
    m = _match(line_text="building permit fee")
    _load(app, [m])

    # Seed the cache BEFORE the matching rule exists, and assert it
    # seeded None -- otherwise a passing test below wouldn't prove the
    # cache was actually cleared rather than never populated.
    assert app.suggest_category(m) is None

    app.add_category_rule(r"\bpermits?\b", "Permits")

    assert app.suggest_category(m) == "Permits"


def test_removing_a_category_rule_invalidates_the_suggestion_cache(app):
    app.add_category_rule(r"\bpermits?\b", "Permits")
    custom_id = next(r.id for r in app.category_rules if not r.built_in)
    m = _match(line_text="building permit fee")
    _load(app, [m])
    assert app.suggest_category(m) == "Permits"

    app.remove_category_rule(custom_id)

    assert app.suggest_category(m) is None


def test_toggling_a_category_rule_off_invalidates_the_suggestion_cache(app):
    m = _match(line_text="materials delivered")
    _load(app, [m])
    assert app.suggest_category(m) == "Materials"
    materials_id = next(r.id for r in app.category_rules if r.id == "materials")

    app.toggle_category_rule(materials_id, False)

    assert app.suggest_category(m) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_category_review.py -v`
Expected: FAIL — `AttributeError: 'App' object has no attribute 'add_category_rule'` (and similarly for `remove_category_rule`/`toggle_category_rule`).

- [ ] **Step 3: Implement `add_category_rule`, `remove_category_rule`, `toggle_category_rule`, and the checkbox-refresh stub they call**

Add these methods to `App`, placed directly after `accept_category_suggestion`/`_after_category_change`/`_refresh_category_widgets` (added in Task 3):

```python
    def add_category_rule(self, pattern_str: str, label: str) -> Optional[str]:
        """Validates and adds a custom category rule. Returns an error
        message on failure (never raises), or None on success."""
        try:
            rule = category_rules.build_custom_rule(
                pattern_str, label or None, self._custom_category_rule_count
            )
        except ValueError as e:
            return str(e)
        self._custom_category_rule_count += 1
        self.category_rules.append(rule)
        self._category_suggestions.clear()
        self._refresh_category_rule_checkboxes()
        return None

    def remove_category_rule(self, rule_id: str) -> None:
        self.category_rules = [r for r in self.category_rules if r.id != rule_id]
        self._category_suggestions.clear()
        self._refresh_category_rule_checkboxes()

    def toggle_category_rule(self, rule_id: str, enabled: bool) -> None:
        for r in self.category_rules:
            if r.id == rule_id:
                r.enabled = enabled
        self._category_suggestions.clear()
        self._refresh_category_rule_checkboxes()

    def _refresh_category_rule_checkboxes(self) -> None:
        # Guarded like _refresh_review_button_state: safe to call before
        # the "Categories" panel (Task 5) has built its container. Task 5
        # extends this method's body once the widget exists; it does not
        # replace this guard.
        if not hasattr(self, "_category_rules_container"):
            return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_category_review.py -v`
Expected: PASS (all 16 tests)

- [ ] **Step 5: Run the full existing suite to confirm nothing else broke**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add cost_extractor/gui.py tests/test_category_review.py
git commit -m "feat: add App.add_category_rule/remove_category_rule/toggle_category_rule"
```

---

## Task 5: `gui.py` — the "Categories" panel, "Categorize Amounts…" window, and README

Mirrors the one working precedent in this codebase for a "suggest + confirm
+ queue" flow — the OCR review window (`open_review_window`,
`_refresh_review_widgets`, `cost_extractor/gui.py:457-527,574-600`) — with
one deliberate difference the spec calls for: `match.line_text` is shown as
a plain-text caption rather than a crop image, since there's no OCR
uncertainty to visualize for a category (the line text is exact, whichever
provenance it came from).

**Files:**
- Modify: `cost_extractor/gui.py:72-92` (`App.__init__`, tail), `:338-455` (`_build_widgets`), `:750-772` (`_refresh_preview_widget`), plus the Task 3/4 stub methods `_refresh_category_widgets`/`_refresh_category_rule_checkboxes`
- Modify: `README.md` (new subsection)
- Test: `tests/test_category_review.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 3-4 (`App.suggest_category`, `confirm_category`, `accept_category_suggestion`, `add_category_rule`, `remove_category_rule`, `toggle_category_rule`, `App.category_rules`).
- Produces: `App.category_queue() -> list[MatchRecord]`, `can_categorize() -> bool`, `current_category_match() -> Optional[MatchRecord]`, `next_category_review()`, `previous_category_review()`, `open_category_window() -> Optional[tk.Toplevel]`. Nothing later depends on these beyond the GUI itself.

- [ ] **Step 1: Write the failing tests for the window and panel**

Append to `tests/test_category_review.py`:

```python
def test_the_category_window_opens_and_shows_the_first_match(app):
    _load(app, [_match(raw_text="$100.00")])

    window = app.open_category_window()

    assert window.winfo_exists()
    assert app.current_category_match().raw_text == "$100.00"


def test_opening_the_category_window_twice_reuses_the_same_window(app):
    _load(app, [_match()])

    first = app.open_category_window()
    second = app.open_category_window()

    assert first is second


def test_the_category_queue_includes_every_match_not_just_ocr(app):
    a = _match(raw_text="$100.00")
    b = _match(raw_text="$200.00")
    _load(app, [a, b])

    assert len(app.category_queue()) == 2


def test_moving_through_the_category_queue_changes_the_shown_match(app):
    _load(app, [_match(raw_text="$100.00"), _match(raw_text="$200.00")])
    app.open_category_window()

    first = app.current_category_match()
    app.next_category_review()

    assert app.current_category_match() is not first


def test_the_category_queue_does_not_run_off_the_end(app):
    _load(app, [_match()])
    app.open_category_window()

    app.next_category_review()
    app.next_category_review()

    assert app.current_category_match() is not None


def test_saving_a_category_through_the_window_advances_the_queue(app):
    _load(app, [_match(raw_text="$100.00"), _match(raw_text="$200.00")])
    app.open_category_window()
    first = app.current_category_match()

    app._category_entry.insert(0, "Materials")
    app._on_save_category()

    assert first.effective_category == "Materials"
    assert app.current_category_match() is not first


def test_the_category_button_is_off_until_a_result_is_loaded(app):
    assert "disabled" in app._category_button.state()

    _load(app, [_match()])
    app._refresh_preview_widget()

    assert "disabled" not in app._category_button.state()


def test_the_categories_panel_lists_the_built_in_rules(app):
    assert len(app._category_rules_container.winfo_children()) == 4  # materials/labor/travel/fees


def test_adding_a_category_rule_through_the_panel_extends_the_checkbox_list(app):
    app._category_pattern_entry.insert(0, r"\bpermits?\b")
    app._category_label_entry.insert(0, "Permits")

    app._on_add_category_rule()

    assert len(app._category_rules_container.winfo_children()) == 5
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_category_review.py -v`
Expected: FAIL — `AttributeError: 'App' object has no attribute 'category_queue'` / `'_category_button'` / `'_category_rules_container'` and similar, since none of this task's widgets or methods exist yet.

- [ ] **Step 3: Add the "Categories" panel to `_build_widgets`**

In `cost_extractor/gui.py`'s `_build_widgets` (after the existing "Money Formats" `rules_frame` block ends — i.e. after line 402's `rules_frame.bind(...)` call — and before `run_frame = ttk.Frame(self.root)` at line 404), add:

```python
        category_rules_frame = ttk.LabelFrame(self.root, text="Categories")
        category_rules_frame.pack(fill="x", padx=8, pady=4)
        self._category_rules_container = ttk.Frame(category_rules_frame)
        self._category_rules_container.pack(fill="x")
        self._category_rule_error_label = ttk.Label(category_rules_frame, foreground="red", text="")
        self._category_rule_error_label.pack(fill="x")

        category_custom_frame = ttk.Frame(category_rules_frame)
        category_custom_frame.pack(fill="x", pady=(4, 0))
        ttk.Label(category_custom_frame, text="Custom pattern:").pack(side="left")
        self._category_pattern_entry = ttk.Entry(category_custom_frame)
        self._category_pattern_entry.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Label(category_custom_frame, text="Label:").pack(side="left")
        self._category_label_entry = ttk.Entry(category_custom_frame, width=15)
        self._category_label_entry.pack(side="left", padx=4)
        ttk.Button(
            category_custom_frame, text="Add", command=self._on_add_category_rule
        ).pack(side="left")

        self._category_hint_label = ttk.Label(
            category_rules_frame,
            foreground="gray",
            text="Regex matched against the line the amount was found on.",
        )
        self._category_hint_label.pack(fill="x", pady=(0, 4))
```

- [ ] **Step 4: Add the "Categorize Amounts…" button to `_build_widgets`**

In the same method's `run_frame` block (after the existing `self._review_button.state(["disabled"])` line, i.e. after line 417, before the "Save Report..." button), add:

```python
        self._category_button = ttk.Button(
            run_frame, text="Categorize Amounts...", command=self.open_category_window
        )
        self._category_button.pack(side="left", padx=4)
        self._category_button.state(["disabled"])
```

- [ ] **Step 5: Add the queue/navigation methods**

Add these methods to `App`, placed after `toggle_category_rule`/`_refresh_category_rule_checkboxes` (Task 4's additions):

```python
    def category_queue(self) -> list[MatchRecord]:
        """Every match, not just OCR-derived ones -- a category applies
        regardless of how the amount was read."""
        if self.last_result is None:
            return []
        return [m for doc in self.last_result.documents for m in doc.matches]

    def can_categorize(self) -> bool:
        return bool(self.category_queue())

    def current_category_match(self) -> Optional[MatchRecord]:
        queue = self.category_queue()
        if not queue:
            return None
        return queue[min(self.category_review_index, len(queue) - 1)]

    def next_category_review(self) -> None:
        queue = self.category_queue()
        if queue:
            self.category_review_index = min(
                self.category_review_index + 1, len(queue) - 1
            )
        self._refresh_category_widgets()

    def previous_category_review(self) -> None:
        self.category_review_index = max(0, self.category_review_index - 1)
        self._refresh_category_widgets()
```

- [ ] **Step 6: Add `open_category_window`**

Add this method to `App`, placed after `open_review_window` (`cost_extractor/gui.py`, after line 527's `return window`):

```python
    def open_category_window(self) -> Optional[tk.Toplevel]:
        """Opens (or raises) the pane for assigning each amount's category."""
        existing = self._category_window
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_set()
            self._refresh_category_widgets()
            return existing

        if not self.can_categorize():
            return None

        window = tk.Toplevel(self.root)
        window.title("Categorize amounts")
        self._category_window = window

        ttk.Label(
            window,
            text=(
                "Every amount needs a category.\nThe category rules "
                "below suggest one from the line the amount was found on."
            ),
            justify="left",
        ).pack(anchor="w", padx=10, pady=(10, 6))

        self._category_caption = ttk.Label(window, justify="left")
        self._category_caption.pack(anchor="w", padx=10)

        suggestion_row = ttk.Frame(window)
        suggestion_row.pack(fill="x", padx=10, pady=(4, 0))
        self._category_suggestion_label = ttk.Label(suggestion_row, justify="left")
        self._category_suggestion_label.pack(side="left")
        self._category_suggestion_button = ttk.Button(
            suggestion_row, text="Use this", command=self._on_accept_category_suggestion
        )

        entry_row = ttk.Frame(window)
        entry_row.pack(fill="x", padx=10, pady=6)
        ttk.Label(entry_row, text="Category:").pack(side="left")
        self._category_entry = ttk.Entry(entry_row, width=24)
        self._category_entry.pack(side="left", padx=6)
        ttk.Button(entry_row, text="Confirm category", command=self._on_save_category).pack(
            side="left"
        )

        note_row = ttk.Frame(window)
        note_row.pack(fill="x", padx=10)
        ttk.Label(note_row, text="Note (optional):").pack(side="left")
        self._category_note_entry = ttk.Entry(note_row, width=40)
        self._category_note_entry.pack(side="left", padx=6, fill="x", expand=True)

        self._category_error = ttk.Label(window, foreground="red")
        self._category_error.pack(anchor="w", padx=10)

        nav = ttk.Frame(window)
        nav.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(nav, text="< Previous", command=self.previous_category_review).pack(
            side="left"
        )
        ttk.Button(nav, text="Next >", command=self.next_category_review).pack(
            side="left", padx=4
        )
        self._category_position = ttk.Label(nav)
        self._category_position.pack(side="left", padx=10)

        self.category_review_index = 0
        self._refresh_category_widgets()
        return window
```

- [ ] **Step 7: Replace the `_refresh_category_widgets` stub with its full body, and add its two helpers**

Replace the Task 3 stub:

```python
    def _refresh_category_widgets(self) -> None:
        # Guards exactly like _refresh_review_widgets: safe to call even
        # when the "Categorize Amounts..." window (Task 5) isn't open.
        # Task 5 extends this method's body once the window exists to
        # refresh; it does not replace this guard.
        window = self._category_window
        if window is None or not window.winfo_exists():
            return
```

with:

```python
    def _refresh_category_widgets(self) -> None:
        window = self._category_window
        if window is None or not window.winfo_exists():
            return

        queue = self.category_queue()
        match = self.current_category_match()
        if match is None:
            self._category_caption.config(text="Nothing left to categorize.")
            self._category_position.config(text="")
            return

        self._category_caption.config(
            text=(
                f'{match.display_name} — {match.location}\n'
                f'line: "{match.line_text}"  {self._category_review_summary(match)}'
            )
        )
        self._category_entry.delete(0, tk.END)
        if match.category_reviewed and match.effective_category is not None:
            self._category_entry.insert(0, match.effective_category)
        self._category_note_entry.delete(0, tk.END)
        self._category_error.config(text="")
        self._category_position.config(
            text=f"{self.category_review_index + 1} of {len(queue)}"
        )
        self._refresh_category_suggestion_widgets(match)

    def _category_review_summary(self, match: MatchRecord) -> str:
        count = len(match.category_revisions)
        if count == 0:
            return "(not yet reviewed)"
        latest = match.category_revisions[-1]
        when = format_revision_timestamp(latest.at)
        note_suffix = f" ({latest.note})" if latest.note else ""
        if count == 1:
            return f"— reviewed once: {latest.value} at {when}{note_suffix}"
        return f"— reviewed {count}x, latest: {latest.value} at {when}{note_suffix}"

    def _refresh_category_suggestion_widgets(self, match: MatchRecord) -> None:
        suggestion = self.suggest_category(match)
        if suggestion is None:
            self._category_suggestion_label.config(
                text="No category suggestion for this line."
            )
            self._category_suggestion_button.pack_forget()
            return
        self._category_suggestion_label.config(text=f"Suggested: {suggestion}")
        self._category_suggestion_button.pack(side="left", padx=8)
```

(Use your editor's exact-match replace on the stub block from Task 3 -- both versions share the same first four lines, so match on the full stub including its trailing guard to avoid replacing the wrong occurrence.)

- [ ] **Step 8: Replace the `_refresh_category_rule_checkboxes` stub with its full body, and add `_on_add_category_rule`**

Replace the Task 4 stub:

```python
    def _refresh_category_rule_checkboxes(self) -> None:
        # Guarded like _refresh_review_button_state: safe to call before
        # the "Categories" panel (Task 5) has built its container. Task 5
        # extends this method's body once the widget exists; it does not
        # replace this guard.
        if not hasattr(self, "_category_rules_container"):
            return
```

with:

```python
    def _refresh_category_rule_checkboxes(self) -> None:
        if not hasattr(self, "_category_rules_container"):
            return
        for child in self._category_rules_container.winfo_children():
            child.destroy()

        for rule in self.category_rules:
            row = ttk.Frame(self._category_rules_container)
            row.pack(fill="x")
            var = tk.BooleanVar(value=rule.enabled)
            cb = ttk.Checkbutton(
                row,
                text=rule.label,
                variable=var,
                command=lambda r=rule, v=var: self.toggle_category_rule(r.id, v.get()),
            )
            cb.pack(side="left")
            if not rule.built_in:
                ttk.Button(
                    row,
                    text="×",
                    width=2,
                    command=lambda rid=rule.id: self.remove_category_rule(rid),
                ).pack(side="left")
```

Add `_on_add_category_rule`, placed near the other `_on_*` event handlers (after `_on_add_custom_pattern`, `cost_extractor/gui.py:850-861`):

```python
    def _on_add_category_rule(self) -> None:
        pattern = self._category_pattern_entry.get().strip()
        label = self._category_label_entry.get().strip()
        if not pattern:
            return
        error = self.add_category_rule(pattern, label)
        if error:
            self._category_rule_error_label.config(text=error)
        else:
            self._category_rule_error_label.config(text="")
            self._category_pattern_entry.delete(0, tk.END)
            self._category_label_entry.delete(0, tk.END)
```

- [ ] **Step 9: Add the remaining category window event handlers**

Add these methods to `App`, placed after `_on_use_second_opinion` (`cost_extractor/gui.py:619-626`):

```python
    def _read_category_note_entry(self) -> Optional[str]:
        return self._category_note_entry.get().strip() or None

    def _on_save_category(self) -> None:
        match = self.current_category_match()
        if match is None:
            return
        error = self.confirm_category(
            match, self._category_entry.get(), note=self._read_category_note_entry()
        )
        self._category_error.config(text=error or "")
        if error is None:
            self.next_category_review()

    def _on_accept_category_suggestion(self) -> None:
        match = self.current_category_match()
        if match is None:
            return
        error = self.accept_category_suggestion(match, note=self._read_category_note_entry())
        self._category_error.config(text=error or "")
        if error is None:
            self.next_category_review()
```

- [ ] **Step 10: Wire the button's enabled state into `_refresh_preview_widget`, and refresh both new widget groups from `__init__`**

Add a new method, placed after `_refresh_review_button_state` (`cost_extractor/gui.py:718-730`):

```python
    def _refresh_category_button_state(self) -> None:
        """Enables the button once a result exists -- every match needs a
        category, so unlike Review Amounts this never depends on whether
        anything was OCR-guessed."""
        if not hasattr(self, "_category_button"):
            return
        if self.can_categorize():
            self._category_button.state(["!disabled"])
        else:
            self._category_button.state(["disabled"])
```

In `_refresh_preview_widget` (`cost_extractor/gui.py:750-757`), add a call to it directly after the existing `self._refresh_review_button_state()` line:

```python
        self._refresh_review_button_state()
        self._refresh_category_button_state()
```

In `App.__init__` (`cost_extractor/gui.py:89-91`), add two calls after the existing `self._refresh_run_button_state()`:

```python
        self._build_widgets()
        self._refresh_rule_checkboxes()
        self._refresh_run_button_state()
        self._refresh_category_rule_checkboxes()
        self._refresh_category_button_state()
```

(Only the last two lines are new; the first three already exist.)

- [ ] **Step 11: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_category_review.py -v`
Expected: PASS (all 25 tests)

- [ ] **Step 12: Run the full existing suite to confirm nothing else broke**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS

- [ ] **Step 13: Document the feature in the README**

Read `README.md`'s "Using the app" section first (specifically "### Checking guessed amounts by eye" and "### Adding a custom money-format pattern") to match its established voice: second-person, concrete, explains "why" with real examples, bold for UI element names, code-formatted for exact values.

Add a new subsection, placed after "### Adding a custom money-format pattern" and before "## Dev setup", covering:

- What the "Categorize Amounts…" window does: every amount, not just OCR-guessed ones, needs a category decision; the app suggests a category from the line of text the amount was found on; **Confirm category** (typed) and **Use this** (when a suggestion exists) are the two ways to resolve a match; **Note (optional)** works the same way it does in the Review Amounts pane — left blank, accepting a suggestion is noted as `confirmed`; typing your own category always records what you typed regardless of any suggestion.
- The built-in starter categories (Materials, Labor, Travel, Fees) are illustrative, not exhaustive — editable and removable in the **Categories** panel, the same way built-in money formats are.
- Adding a custom category pattern: mirror the "Adding a custom money-format pattern" section's structure, but note the key difference — a category rule is a plain presence-detection pattern (no required named group; any regex that matches somewhere on the line counts), matched against the same line as the amount, not the whole page.
- The report's new columns/sheet: Details gains **Category** / **Category Review** columns (a confirmed category, a suggested-but-unconfirmed one shown as `"{label} (suggested, unconfirmed)"`, or `"Uncategorized"`); the Revisions sheet gains a **Dimension** column distinguishing a money-value correction from a category decision; the new **Categories** sheet is the per-category breakdown — one row per (category, status) pair, confirmed and unconfirmed totals kept separate; the Summary sheet gets an **Amounts not yet categorized** count.

Keep it proportionate to the existing sections (a few hundred words) — this is documentation, not the spec.

- [ ] **Step 14: Commit**

```bash
git add cost_extractor/gui.py tests/test_category_review.py README.md
git commit -m "feat: add the Categories panel and Categorize Amounts window"
```

---

## Task 6: `report.py` — `build_workbook`'s new parameter, Details columns, Summary row

**Files:**
- Modify: `cost_extractor/report.py:1-56` (imports, `review_label`), `:21-33` (`_DETAILS_HEADER`), `:105-165` (`build_workbook`)
- Modify (mechanical ripple): `tests/test_report.py` (`test_details_sheet_lists_every_match`)
- Test: `tests/test_report_categorization.py` (new)

**Interfaces:**
- Consumes: `category_rules.suggest_category` (Task 1). `MatchRecord.line_text`, `category_reviewed`, `effective_category`; `PipelineResult.uncategorized_count` (Task 2).
- Produces: `build_workbook(result, category_rules=None)` (new optional 2nd parameter — every existing 1-argument call site keeps compiling). `category_label(match, rules) -> str`. Used by Task 7 (Revisions/Categories additions to the same function).

**Design note:** `build_workbook`'s new parameter is deliberately named `category_rules`, matching the module-level `from cost_extractor import category_rules` import — inside `build_workbook`'s own body this parameter shadows the module for the duration of that one function, same as `date_rules` does on the sibling `spend-over-time` branch. This is safe here for the same reason it's safe there: nothing inside `build_workbook`'s own body ever calls `category_rules.<something>()` directly — it only passes the local `active_category_rules` list along to `category_label`, a separate top-level function that sees the un-shadowed module. Unlike the date-suggestion case, `category_label`'s suggestion lookup operates on `match.line_text` (already scoped to one line, captured once at extraction time) rather than re-scanning a whole document per match, so there is no analogous per-document hoisting to add here — do not add one speculatively.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_report_categorization.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_report_categorization.py -v`
Expected: FAIL — `TypeError: build_workbook() takes 1 positional argument but 2 were given`.

- [ ] **Step 3: Add the `category_rules` import, `build_workbook`'s new parameter, and `category_label`**

In `cost_extractor/report.py`, add to the imports (after the existing `from cost_extractor.revisions import format_revision_timestamp` on line 11):

```python
from cost_extractor import category_rules
```

Add `category_label`, placed after `review_label` (after line 56's `return REVIEW_FLAG if match.value_needs_review else None`, before `def _as_number(value) -> float:`):

```python
def category_label(match, rules: list["CategoryRule"]) -> str:
    if match.category_reviewed:
        return match.effective_category
    suggestion = category_rules.suggest_category(match.line_text, rules)
    return f"{suggestion} (suggested, unconfirmed)" if suggestion else "Uncategorized"
```

Change `build_workbook`'s signature (line 105) from:

```python
def build_workbook(result: PipelineResult) -> Workbook:
    wb = Workbook()
```

to:

```python
def build_workbook(
    result: PipelineResult, category_rules: Optional[list["CategoryRule"]] = None
) -> Workbook:
    active_category_rules = category_rules or []
    wb = Workbook()
```

- [ ] **Step 4: Add `_DETAILS_HEADER`'s two new columns and wire `category_label` into the Details loop**

Change `_DETAILS_HEADER` (`cost_extractor/report.py:21-33`) from:

```python
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
```

to:

```python
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
    "Category",
    "Category Review",
]
```

In `build_workbook`'s Details loop (`cost_extractor/report.py:148-164`), change:

```python
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
                    m.raw_text if m.value_reviewed else None,
                ]
            )
```

to:

```python
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
                    m.raw_text if m.value_reviewed else None,
                    category_label(m, active_category_rules),
                    REVIEW_FLAG if not m.category_reviewed else None,
                ]
            )
```

- [ ] **Step 5: Add the Summary row**

In `build_workbook` (`cost_extractor/report.py:138-146`), after the existing `"Guessed amounts not yet checked"` row append and before `details_ws = wb.create_sheet("Details")`, add:

```python
    summary_ws.append(
        [
            "Amounts not yet categorized",
            None,
            None,
            result.uncategorized_count,
            None,
        ]
    )
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_report_categorization.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 7: Fix the mechanical ripple in `tests/test_report.py`**

`_DETAILS_HEADER` gaining two columns breaks `test_details_sheet_lists_every_match`'s exact-list assertions (it is the one Details test in the whole suite that checks the header and rows by positional equality rather than `header.index(...)` lookup). In `tests/test_report.py`, replace:

```python
def test_details_sheet_lists_every_match():
    wb = build_workbook(_sample_result())
    ws = wb["Details"]

    header = [c.value for c in ws[1]]
    assert header == [
        "Source File",
        "Location",
        "Matched Text",
        "Rule",
        "Value",
        "Source",
        "Confidence",
        "Review",
        "Read As Text",
    ]

    # These fixtures come from a text layer, so they carry no score and
    # nothing is flagged.
    row2 = [c.value for c in ws[2]]
    assert row2 == [
        "invoice.docx", "paragraph 1", "$1,234.56", "standard", 1234.56,
        "text", None, None, None,
    ]

    row3 = [c.value for c in ws[3]]
    assert row3 == [
        "invoice.docx", "table 1, row 1, col 2", "($500)", "paren_negative", -500,
        "text", None, None, None,
    ]
```

with:

```python
def test_details_sheet_lists_every_match():
    wb = build_workbook(_sample_result())
    ws = wb["Details"]

    header = [c.value for c in ws[1]]
    assert header == [
        "Source File",
        "Location",
        "Matched Text",
        "Rule",
        "Value",
        "Source",
        "Confidence",
        "Review",
        "Read As Text",
        "Category",
        "Category Review",
    ]

    # These fixtures come from a text layer, so they carry no score and
    # nothing is flagged. Neither match has a category confirmed, and
    # _sample_result()'s matches carry no line_text, so nothing can be
    # suggested either -- both read "Uncategorized"/REVIEW.
    row2 = [c.value for c in ws[2]]
    assert row2 == [
        "invoice.docx", "paragraph 1", "$1,234.56", "standard", 1234.56,
        "text", None, None, None, "Uncategorized", "REVIEW",
    ]

    row3 = [c.value for c in ws[3]]
    assert row3 == [
        "invoice.docx", "table 1, row 1, col 2", "($500)", "paren_negative", -500,
        "text", None, None, None, "Uncategorized", "REVIEW",
    ]
```

- [ ] **Step 8: Run the full existing suite to confirm nothing else broke**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS. (`test_report_evidence.py`'s Details tests all use `header.index(...)` lookups, per the file's own established convention, so the two new trailing columns don't disturb them.)

- [ ] **Step 9: Commit**

```bash
git add cost_extractor/report.py tests/test_report_categorization.py tests/test_report.py
git commit -m "feat: export Category/Category Review columns and a Summary row"
```

---

## Task 7: `report.py` — Revisions `Dimension` column and the new "Categories" sheet

**Files:**
- Modify: `cost_extractor/report.py:1-11` (imports), `:66-102` (`_REVISIONS_HEADER`, `_revision_rows`), `:105-173` (`build_workbook`)
- Modify (mechanical ripple): `tests/test_report.py` (`test_build_workbook_has_details_and_summary_sheets`), `tests/test_report_evidence.py` (`test_revisions_sheet_header`)
- Test: `tests/test_report_categorization.py` (append)

**Interfaces:**
- Consumes: everything from Task 6 (`build_workbook`'s `category_rules` parameter and `active_category_rules`), Task 2 (`MatchRecord.category_revisions`, `effective_category`, `category_reviewed`, `effective_value`).
- Produces: `_category_revision_rows(match) -> list[list]`, `_category_summary_rows(result, rules) -> list[list]`. Nothing later depends on these.

**Sheet placement:** unlike a chronological rollup, the Categories sheet sits immediately after Details and before Revisions — `["Summary", "Details", "Categories", "Revisions"]` — not last. Categories is a per-match-attribute summary (like Details), not the kind of terminal, synthesized view a chronological sheet would be.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_report_categorization.py`:

```python
def test_revisions_sheet_gets_a_category_dimension_row(tmp_path):
    m = _match()
    record_revision(m.category_revisions, "Materials", note="from invoice", now=_NOW)
    ws = _sheet(tmp_path, _result([m]), "Revisions")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Dimension")] == "Category"
    assert row[header.index("Revised From")] == "Uncategorized"
    assert row[header.index("Revised To")] == "Materials"
    assert row[header.index("Note")] == "from invoice"


def test_a_value_revision_row_reads_value_for_dimension(tmp_path):
    m = _match()
    record_revision(m.value_revisions, Decimal("150.00"), now=_NOW)
    ws = _sheet(tmp_path, _result([m]), "Revisions")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Dimension")] == "Value"


def test_a_second_category_confirmation_shows_two_revision_rows(tmp_path):
    m = _match()
    first = datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc)
    second = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)
    record_revision(m.category_revisions, "Materials", now=first)
    record_revision(m.category_revisions, "Labor", note="fixed", now=second)
    ws = _sheet(tmp_path, _result([m]), "Revisions")
    header = [c.value for c in ws[1]]
    row1 = [c.value for c in ws[2]]
    row2 = [c.value for c in ws[3]]

    assert row1[header.index("Revised From")] == "Uncategorized"
    assert row1[header.index("Revised To")] == "Materials"
    assert row2[header.index("Revised From")] == "Materials"
    assert row2[header.index("Revised To")] == "Labor"


def test_a_match_with_both_value_and_category_history_shows_value_before_category(tmp_path):
    # Both dimensions on one match, in one Revisions block: Value history
    # must read before Category history, per the spec's ordering rule.
    m = _match()
    record_revision(m.value_revisions, Decimal("150.00"), now=_NOW)
    record_revision(m.category_revisions, "Materials", now=_NOW)
    ws = _sheet(tmp_path, _result([m]), "Revisions")
    header = [c.value for c in ws[1]]
    row1 = [c.value for c in ws[2]]
    row2 = [c.value for c in ws[3]]

    assert row1[header.index("Dimension")] == "Value"
    assert row2[header.index("Dimension")] == "Category"


def test_categories_sheet_lists_confirmed_and_unconfirmed_rows(tmp_path):
    from cost_extractor.category_rules import default_rules

    confirmed = _match(value="100.00")
    record_revision(confirmed.category_revisions, "Materials", now=_NOW)
    unconfirmed = _match(value="50.00", line_text="labor charges")
    ws = _sheet(
        tmp_path, _result([confirmed, unconfirmed]), "Categories", rules=default_rules()
    )
    rows = {
        (row[0], row[1]): (row[2], row[3])
        for row in ws.iter_rows(min_row=2, values_only=True)
    }

    assert rows[("Materials", "Confirmed")] == (100.0, 1)
    assert rows[("Labor", "Unconfirmed")] == (50.0, 1)


def test_categories_sheet_uncategorized_row_omitted_when_none(tmp_path):
    m = _match()
    record_revision(m.category_revisions, "Materials", now=_NOW)
    ws = _sheet(tmp_path, _result([m]), "Categories")
    categories = [row[0] for row in ws.iter_rows(min_row=2, values_only=True)]

    assert "Uncategorized" not in categories


def test_categories_sheet_uncategorized_row_present_when_no_signal(tmp_path):
    m = _match()  # no line_text, no rules passed -- no suggestion possible
    ws = _sheet(tmp_path, _result([m]), "Categories")
    rows = {row[0]: (row[2], row[3]) for row in ws.iter_rows(min_row=2, values_only=True)}

    assert rows["Uncategorized"] == (100.0, 1)


def test_categories_sheet_exists_header_only_with_zero_matches(tmp_path):
    ws = _sheet(tmp_path, _result([]), "Categories")

    assert ws.max_row == 1


def test_build_workbook_gains_the_categories_sheet(tmp_path):
    wb = build_workbook(_result([]))

    assert wb.sheetnames == ["Summary", "Details", "Categories", "Revisions"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_report_categorization.py -v`
Expected: FAIL — `KeyError: "Worksheet Categories does not exist."` and `ValueError: 'Dimension' is not in list` for the Revisions tests.

- [ ] **Step 3: Add the `Decimal` import, the `Dimension` column, and `_category_revision_rows`**

In `cost_extractor/report.py`, add to the imports (after the existing `from pathlib import Path` on line 5):

```python
from decimal import Decimal
```

Change `_REVISIONS_HEADER` (`cost_extractor/report.py:66-75`) from:

```python
_REVISIONS_HEADER = [
    "Source File",
    "Location",
    "Matched Text",
    "Rule",
    "Revised From",
    "Revised To",
    "Timestamp",
    "Note",
]
```

to:

```python
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
```

Change `_revision_rows` (`cost_extractor/report.py:78-102`) to insert `"Value"` as the row's 5th element, matching the header's new position -- from:

```python
def _revision_rows(match) -> list[list]:
    """One row per revision event for one match, in order.

    "Revised From" is the value immediately before that revision: the
    match's original reading for the first revision, the previous
    revision's value for every one after -- so reading down a match's
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
                _as_number(previous),
                _as_number(revision.value),
                format_revision_timestamp(revision.at),
                revision.note,
            ]
        )
        previous = revision.value
    return rows
```

to:

```python
def _revision_rows(match) -> list[list]:
    """One row per revision event for one match, in order.

    "Revised From" is the value immediately before that revision: the
    match's original reading for the first revision, the previous
    revision's value for every one after -- so reading down a match's
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


def _category_revision_rows(match) -> list[list]:
    """One row per category-revision event, same chaining rule as
    _revision_rows: "Revised From" is the value immediately before that
    revision -- None ("Uncategorized") for the first one, the previous
    revision's category for every one after."""
    rows = []
    previous = None
    for revision in match.category_revisions:
        rows.append(
            [
                match.display_name,
                match.location,
                match.raw_text,
                match.rule_id,
                "Category",
                previous or "Uncategorized",
                revision.value or "Uncategorized",
                format_revision_timestamp(revision.at),
                revision.note,
            ]
        )
        previous = revision.value
    return rows
```

- [ ] **Step 4: Wire `_category_revision_rows` into `build_workbook`'s Revisions loop**

Change (`cost_extractor/report.py:166-171`) from:

```python
    revisions_ws = wb.create_sheet("Revisions")
    revisions_ws.append(_REVISIONS_HEADER)
    for doc in result.documents:
        for m in doc.matches:
            for row in _revision_rows(m):
                revisions_ws.append(row)

    return wb
```

to:

```python
    revisions_ws = wb.create_sheet("Revisions")
    revisions_ws.append(_REVISIONS_HEADER)
    for doc in result.documents:
        for m in doc.matches:
            for row in _revision_rows(m):
                revisions_ws.append(row)
            for row in _category_revision_rows(m):
                revisions_ws.append(row)

    return wb
```

- [ ] **Step 5: Add `_category_summary_rows` and the new sheet, placed between Details and Revisions**

Add, placed after `_category_revision_rows` (just written) and before `def build_workbook(...)`:

```python
_CATEGORIES_HEADER = ["Category", "Status", "Amount", "Match Count"]


def _category_summary_rows(
    result: PipelineResult, rules: list["category_rules.CategoryRule"]
) -> list[list]:
    """One row per (category, status) pair. Confirmed rows sum
    effective_value for matches whose effective_category matches;
    unconfirmed rows sum matches whose live suggestion matches but
    aren't confirmed yet; an Uncategorized row covers matches with
    neither, omitted (not a zero row) when there are none."""
    confirmed: dict[str, tuple[Decimal, int]] = {}
    unconfirmed: dict[str, tuple[Decimal, int]] = {}
    uncategorized_total = Decimal("0")
    uncategorized_count = 0

    for doc in result.documents:
        for m in doc.matches:
            if m.category_reviewed:
                bucket, key = confirmed, m.effective_category
            else:
                suggestion = category_rules.suggest_category(m.line_text, rules)
                if suggestion is None:
                    uncategorized_total += m.effective_value
                    uncategorized_count += 1
                    continue
                bucket, key = unconfirmed, suggestion
            total, count = bucket.get(key, (Decimal("0"), 0))
            bucket[key] = (total + m.effective_value, count + 1)

    rows = []
    for category in sorted(set(confirmed) | set(unconfirmed)):
        if category in confirmed:
            total, count = confirmed[category]
            rows.append([category, "Confirmed", _as_number(total), count])
        if category in unconfirmed:
            total, count = unconfirmed[category]
            rows.append([category, "Unconfirmed", _as_number(total), count])
    if uncategorized_count:
        rows.append(
            ["Uncategorized", "(no signal)", _as_number(uncategorized_total), uncategorized_count]
        )
    return rows
```

Change `build_workbook` to insert the Categories sheet between the existing Details block and the existing Revisions block. The code directly before this insertion point (`cost_extractor/report.py`, immediately after the Details loop's closing) is:

```python
    revisions_ws = wb.create_sheet("Revisions")
```

Insert the Categories sheet immediately before that line:

```python
    categories_ws = wb.create_sheet("Categories")
    categories_ws.append(_CATEGORIES_HEADER)
    for row in _category_summary_rows(result, active_category_rules):
        categories_ws.append(row)

    revisions_ws = wb.create_sheet("Revisions")
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_report_categorization.py -v`
Expected: PASS (all 13 tests)

- [ ] **Step 7: Fix the two remaining mechanical ripples**

In `tests/test_report_evidence.py`, `test_revisions_sheet_header` asserts `_REVISIONS_HEADER`'s exact contents. Replace:

```python
    assert [c.value for c in ws[1]] == [
        "Source File", "Location", "Matched Text", "Rule",
        "Revised From", "Revised To", "Timestamp", "Note",
    ]
```

with:

```python
    assert [c.value for c in ws[1]] == [
        "Source File", "Location", "Matched Text", "Rule", "Dimension",
        "Revised From", "Revised To", "Timestamp", "Note",
    ]
```

In `tests/test_report.py`, `test_build_workbook_has_details_and_summary_sheets` asserts the exact sheet list. Replace:

```python
    assert wb.sheetnames == ["Summary", "Details", "Revisions"]
```

with:

```python
    assert wb.sheetnames == ["Summary", "Details", "Categories", "Revisions"]
```

- [ ] **Step 8: Run the full existing suite to confirm nothing else broke**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add cost_extractor/report.py tests/test_report_categorization.py tests/test_report.py tests/test_report_evidence.py
git commit -m "feat: add the Revisions Dimension column and the Categories sheet"
```

---

## Final Verification

- [ ] Run the entire suite once more from a clean tree: `.venv/Scripts/python.exe -m pytest -v`
- [ ] Manually smoke-test the GUI: run the app, load a document containing recognizable category keywords (e.g. "materials", "labor") near dollar amounts, open "Categorize Amounts…", verify a suggestion appears, confirm a category, add a custom category pattern, export a report, and open the `.xlsx` to check the Details/Categories/Revisions sheets by eye.
- [ ] Confirm every pre-existing `build_workbook(` call site (grep `build_workbook(` across `cost_extractor/` and `tests/`) still compiles with the new parameter defaulted away.
- [ ] Whole-branch review: this sub-project's `App.suggest_category` operates on `match.line_text` (already scoped per-match at extraction time), unlike the sibling `spend-over-time` branch's `App.suggest_spend_date`, which searches a whole document -- confirm during the final review that this difference was correctly NOT "fixed" by adding unneeded document-level caching or hoisting to `report.py`'s Categories-sheet computation (see Task 6's design note).
