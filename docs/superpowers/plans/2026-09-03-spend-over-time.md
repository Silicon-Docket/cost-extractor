# Spend Over Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let every extracted amount be assigned a human-confirmed spend date, and export a genuinely chronological "Spend By Month" view of the totals.

**Architecture:** A new `date_rules.py` module (pattern+parser rule engine, mirroring `money_parser.py`) finds date-shaped text anywhere in a document; `pipeline.py` captures each document's full text and each match's offset into it so a suggestion can be computed on demand; `gui.py` adds a suggest/confirm workflow (a third instance of the pattern from OCR review) plus a rule-management panel; `report.py` exports the confirmed/suggested state per match and a new chronological rollup sheet.

**Tech Stack:** Python 3, Tkinter (stdlib), `re`, `dataclasses`, `datetime.date`, openpyxl. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-03-spend-over-time-design.md`

## Global Constraints

- Built-in date formats: numeric only, `MM/DD/YYYY` and `MM-DD-YYYY`, four-digit year, US convention (month before day). No ISO 8601, no written-month formats as built-ins.
- Every date suggestion is computed on demand from `DocumentResult.full_text` + the live rule set — never stored eagerly, never cached across a rule-set change without invalidation.
- `nearest_date` must never substitute a more distant, parseable candidate when the truly nearest date-shaped text failed to parse — it reports "no suggestion" instead.
- Every match's spend-date state is one of exactly three outcomes, and every piece of UI/export code that branches on it must handle all three: confirmed date, confirmed no-date (`spend_date_reviewed=True`, `effective_spend_date=None`), not-yet-reviewed.
- `build_workbook`'s existing single-argument call sites must keep compiling and behaving unchanged (the new `date_rules` parameter is optional, defaulting to `None`/`[]`).
- No auto-committing a suggestion to the time-series without explicit human confirmation. No multi-date assignment. No recording *who* made a decision, no full UI-interaction logging — a note plus a timestamp is the audit bar, per sub-project 1.
- This branch (`spend-over-time`) is forked from `main` and does not contain sub-project 2's (`spend-categorization`) category machinery — do not reference `category_rules`, `CategoryRule`, or a `Dimension` column value other than `"Value"`/`"Spend Date"` anywhere in this plan's code. The Revisions sheet's `Dimension` column is being introduced *by this plan*, not extended.

---

## File Structure

- `cost_extractor/date_rules.py` (new) — the date rule engine: `DateRule`, `DateMatch`, `default_rules`, `find_dates`, `nearest_date`, `build_custom_rule`.
- `cost_extractor/pipeline.py` (modify) — `DocumentResult.full_text`, `MatchRecord.doc_offset`/`spend_date_revisions`/`spend_date_reviewed`/`effective_spend_date`, `PipelineResult.unreviewed_date_count`, and the `_process_single_file` loop rework that computes `full_text`/`doc_offset`.
- `cost_extractor/gui.py` (modify) — `App` state, suggest/confirm methods, rule-management methods, the "Date Formats" panel and "Confirm Spend Dates…" window widgets.
- `cost_extractor/report.py` (modify) — `build_workbook`'s new parameter, Details sheet columns, Summary row, Revisions sheet `Dimension` column, new "Spend By Month" sheet.
- `tests/test_date_rules.py` (new) — the rule engine, standalone.
- `tests/test_spend_dates.py` (new) — `pipeline.py`-level: `doc_offset`/`full_text` capture, `MatchRecord`/`PipelineResult` properties.
- `tests/test_date_review.py` (new) — `gui.py`-level: suggest/confirm, rule management, cache invalidation, the window widgets.
- `tests/test_report.py`, `tests/test_report_evidence.py` (modify) — mechanical ripple: sheet name lists, `_REVISIONS_HEADER` assertion.
- `tests/test_report_spend_dates.py` (new) — `report.py`-level: Details columns, Summary row, Revisions `Dimension` rows, Spend By Month sheet.

---

## Task 1: `date_rules.py` — the date rule engine

**Files:**
- Create: `cost_extractor/date_rules.py`
- Test: `tests/test_date_rules.py`

**Interfaces:**
- Consumes: `cost_extractor.money_parser._is_pattern_too_slow(compiled: re.Pattern) -> bool` (existing, reused unchanged).
- Produces: `DateRule` (dataclass: `id: str`, `label: str`, `pattern: str`, `parser: Callable[[re.Match], Optional[date]]`, `priority: int = 50`, `enabled: bool = True`, `built_in: bool = True`, `flags: int = 0`, `compiled: re.Pattern` (post-init)). `DateMatch` (frozen dataclass: `value: Optional[date]`, `raw_text: str`, `start: int`). `default_rules() -> list[DateRule]`. `find_dates(text: str, rules: list[DateRule]) -> list[DateMatch]`. `nearest_date(candidates: list[DateMatch], target_offset: int) -> Optional[DateMatch]`. `build_custom_rule(pattern_str: str, label: Optional[str], index: int) -> DateRule` (raises `ValueError` on invalid input).

- [ ] **Step 1: Write the failing tests for the core parser and default rule**

Create `tests/test_date_rules.py`:

```python
from datetime import date

import pytest

from cost_extractor.date_rules import (
    DateMatch,
    build_custom_rule,
    default_rules,
    find_dates,
    nearest_date,
)


def test_default_rules_matches_slash_separated_numeric_date():
    matches = find_dates("Invoice dated 06/14/2026 for services", default_rules())

    assert len(matches) == 1
    assert matches[0].value == date(2026, 6, 14)
    assert matches[0].raw_text == "06/14/2026"


def test_default_rules_matches_dash_separated_numeric_date():
    matches = find_dates("Dated 06-14-2026", default_rules())

    assert len(matches) == 1
    assert matches[0].value == date(2026, 6, 14)


def test_default_rules_uses_month_before_day_convention():
    # 03/04/2026 is March 4th, not April 3rd -- US convention, not a bug.
    matches = find_dates("On 03/04/2026 the invoice was issued", default_rules())

    assert matches[0].value == date(2026, 3, 4)


def test_a_calendar_invalid_date_is_kept_with_a_none_value_not_dropped():
    # 13/40/2026: digit-count-plausible (\\d{1,2} admits it) but not a real
    # date. The match must survive with value=None, not vanish entirely.
    matches = find_dates("Ref 13/40/2026 on file", default_rules())

    assert len(matches) == 1
    assert matches[0].value is None
    assert matches[0].raw_text == "13/40/2026"


def test_default_rules_returns_fresh_instances_each_call():
    first_call = default_rules()
    for rule in first_call:
        rule.enabled = False

    second_call = default_rules()

    assert all(rule.enabled for rule in second_call)


def test_disabled_rule_produces_no_matches():
    rules = default_rules()
    for rule in rules:
        rule.enabled = False

    assert find_dates("Dated 06/14/2026", rules) == []


def test_no_date_shaped_text_produces_no_matches():
    assert find_dates("No dates mentioned here at all.", default_rules()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_date_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cost_extractor.date_rules'`

- [ ] **Step 3: Implement `DateRule`, `_parse_named_groups`, `default_rules`, `DateMatch`, `find_dates` (without overlap resolution yet)**

Create `cost_extractor/date_rules.py`:

```python
"""Regex-based rule engine for detecting spend dates in text.

Closer to money_parser.py's shape than category_rules.py's: a date rule
must both find text AND turn it into a real `date`, so it needs a parser
callback the same way MoneyFormatRule needs a normalizer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional


@dataclass
class DateRule:
    id: str
    label: str
    pattern: str
    parser: Callable[[re.Match], Optional[date]]
    priority: int = 50
    enabled: bool = True
    built_in: bool = True
    flags: int = 0  # NOT case-insensitive by default: this module's only
                     # patterns (built-in and custom) are digits/separators,
                     # which have no case. A future letter-containing custom
                     # pattern sets its own flags via re.compile's usual
                     # inline (?i) syntax inside the pattern string itself.
    compiled: re.Pattern = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.compiled = re.compile(self.pattern, self.flags)


def _parse_named_groups(match: re.Match) -> Optional[date]:
    """The one parser every date rule (built-in and custom) uses: reads
    year/month/day by NAME, not position, so a custom pattern can place
    them in any order (day-first, month-first) and still be interpreted
    correctly by the same function -- mirroring money_parser's
    generic_normalizer. Returns None (never raises) for a numerically
    plausible but calendar-invalid date, e.g. 13/40/2026 -- the pattern's
    \\d{1,2} groups admit strings date() itself rejects."""
    try:
        return date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
    except ValueError:
        return None


_NUMERIC_DATE_PATTERN = r"(?<!\d)(?P<month>\d{1,2})[/-](?P<day>\d{1,2})[/-](?P<year>\d{4})(?!\d)"


def default_rules() -> list[DateRule]:
    """Fresh instances every call -- same mutation-isolation reasoning as
    money_parser.default_rules(): DateRule.enabled is mutated in place by
    the GUI, so callers must never share a cached/module-level list."""
    return [
        DateRule(
            id="numeric_date",
            label="Numeric date (MM/DD/YYYY, MM-DD-YYYY)",
            pattern=_NUMERIC_DATE_PATTERN,
            parser=_parse_named_groups,
            priority=0,
        ),
    ]


@dataclass(frozen=True)
class DateMatch:
    value: Optional[date]  # None if this regex match couldn't be parsed --
                            # kept, not dropped, so a calendar-invalid date
                            # sitting right next to an amount is visible as
                            # "something was here but unreadable" rather
                            # than invisible.
    raw_text: str  # the literal matched substring, e.g. "06/14/2026".
    start: int  # character offset into the text that was searched


def find_dates(text: str, rules: list[DateRule]) -> list[DateMatch]:
    """Every date-shaped match in `text`, across all enabled rules --
    including ones that matched the pattern but failed to parse
    (value=None). Overlapping matches from different rules are resolved
    like find_money_matches: sorted by position, then by match length
    (longest wins), then by rule priority (lowest wins); accepted
    greedily, left to right, never overlapping."""
    candidates = []
    rule_by_span: dict[tuple[int, int], DateRule] = {}
    for rule in rules:
        if not rule.enabled:
            continue
        for m in rule.compiled.finditer(text):
            span = (m.start(), m.end())
            candidates.append((span[0], span[1], rule.priority, m))
            rule_by_span[span] = rule

    candidates.sort(key=lambda c: (c[0], -(c[1] - c[0]), c[2]))
    accepted: list[DateMatch] = []
    cursor = 0
    for start, end, _, m in candidates:
        if start >= cursor:
            rule = rule_by_span[(start, end)]
            accepted.append(DateMatch(value=rule.parser(m), raw_text=m.group(0), start=start))
            cursor = end
    return accepted
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_date_rules.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Write the failing tests for overlap resolution and `nearest_date`**

Append to `tests/test_date_rules.py`:

```python
def test_find_dates_resolves_overlap_by_priority():
    # A custom rule overlapping the built-in one on the same span: lower
    # priority wins, same tie-break as find_money_matches.
    custom = build_custom_rule(
        r"(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})", "Custom", 0
    )
    custom.priority = -1  # deliberately beats the built-in's priority=0

    rules = default_rules() + [custom]
    matches = find_dates("Dated 06/14/2026", rules)

    assert len(matches) == 1  # exactly one survives, not both


def test_find_dates_returns_matches_in_text_order():
    matches = find_dates("06/14/2026 then later 08/01/2026", default_rules())

    assert [m.raw_text for m in matches] == ["06/14/2026", "08/01/2026"]


def test_nearest_date_picks_the_closer_candidate():
    candidates = [
        DateMatch(value=date(2026, 1, 1), raw_text="01/01/2026", start=0),
        DateMatch(value=date(2026, 6, 14), raw_text="06/14/2026", start=100),
    ]

    result = nearest_date(candidates, target_offset=95)

    assert result.value == date(2026, 6, 14)


def test_nearest_date_breaks_a_tie_toward_the_earlier_candidate():
    candidates = [
        DateMatch(value=date(2026, 1, 1), raw_text="01/01/2026", start=40),
        DateMatch(value=date(2026, 6, 14), raw_text="06/14/2026", start=60),
    ]

    # target_offset=50 is exactly 10 from each -- a genuine tie.
    result = nearest_date(candidates, target_offset=50)

    assert result.start == 40


def test_nearest_date_returns_none_with_no_candidates():
    assert nearest_date([], target_offset=0) is None


def test_nearest_date_does_not_substitute_a_distant_valid_date_for_a_closer_invalid_one():
    # The direct regression test for the substitution bug this design
    # fixes: the CLOSEST candidate failed to parse. nearest_date must
    # report it (value=None), not skip past it to the farther valid one.
    candidates = [
        DateMatch(value=None, raw_text="13/40/2026", start=48),  # closest, invalid
        DateMatch(value=date(2026, 1, 1), raw_text="01/01/2026", start=500),  # far, valid
    ]

    result = nearest_date(candidates, target_offset=50)

    assert result.raw_text == "13/40/2026"
    assert result.value is None
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `pytest tests/test_date_rules.py -v`
Expected: FAIL — `nearest_date`/`build_custom_rule` not defined yet; the overlap test fails with `NameError: build_custom_rule`.

- [ ] **Step 7: Implement `nearest_date` and `build_custom_rule`**

Append to `cost_extractor/date_rules.py`:

```python
def nearest_date(candidates: list[DateMatch], target_offset: int) -> Optional[DateMatch]:
    """The single date-shaped match closest to `target_offset`, by
    absolute character distance -- or None if there are no candidates at
    all. Deliberately does NOT skip past an unparseable-but-closer
    candidate to reach a more distant, parseable one: if the nearest
    date-shaped text to an amount couldn't be read as a real date, this
    reports "no suggestion" (the caller checks `.value is None`), not a
    confident, wrong substitute from elsewhere in the document. Ties
    resolve to whichever appears EARLIER in the text -- a stable,
    deterministic rule."""
    if not candidates:
        return None
    return min(candidates, key=lambda c: (abs(c.start - target_offset), c.start))


def build_custom_rule(pattern_str: str, label: Optional[str], index: int) -> DateRule:
    """Validates and builds a user-supplied date rule. A custom pattern
    must supply named groups (?P<year>...), (?P<month>...), (?P<day>...)
    -- the pattern says WHERE the pieces are; the one shared
    _parse_named_groups says what to do with them, so a day-first pattern
    just names its groups in a different order. Raises ValueError with a
    user-facing message on invalid regex or a missing required group;
    never lets re.error escape to the GUI."""
    from cost_extractor.money_parser import _is_pattern_too_slow  # shared guard

    try:
        compiled = re.compile(pattern_str)
    except re.error as e:
        raise ValueError(f"Invalid regex: {e}") from e
    missing = {"year", "month", "day"} - set(compiled.groupindex)
    if missing:
        raise ValueError(
            "Pattern must include named groups "
            "(?P<year>...), (?P<month>...), (?P<day>...) "
            f"-- missing: {', '.join(sorted(missing))}"
        )
    if _is_pattern_too_slow(compiled):
        raise ValueError(
            "Pattern is too slow / potentially catastrophic backtracking; simplify it."
        )
    return DateRule(
        id=f"custom_{index}",
        label=label or f"Custom date {index}",
        pattern=pattern_str,
        parser=_parse_named_groups,
        priority=100 + index,
        enabled=True,
        built_in=False,
    )
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_date_rules.py -v`
Expected: PASS (all 13 tests)

- [ ] **Step 9: Write the failing tests for `build_custom_rule`'s validation**

Append to `tests/test_date_rules.py`:

```python
def test_build_custom_rule_accepts_a_day_first_pattern():
    # A day-first document (Non-goal as a built-in) is usable via a custom
    # pattern that just names its groups in a different order.
    rule = build_custom_rule(
        r"(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})", "Day-first", 0
    )

    matches = find_dates("Dated 14.06.2026", [rule])

    assert matches[0].value == date(2026, 6, 14)
    assert rule.built_in is False
    assert rule.enabled is True


def test_build_custom_rule_rejects_invalid_regex_syntax():
    with pytest.raises(ValueError, match="Invalid regex"):
        build_custom_rule(r"(?P<year>\d{4}", None, 0)


def test_build_custom_rule_rejects_a_pattern_missing_a_required_group():
    with pytest.raises(ValueError, match="year"):
        build_custom_rule(r"(?P<month>\d{1,2})/(?P<day>\d{1,2})", None, 0)


def test_build_custom_rule_rejects_catastrophic_backtracking_pattern():
    with pytest.raises(ValueError, match="slow|backtrack"):
        build_custom_rule(
            r"(?P<year>(a+)+)(?P<month>\d{1,2})(?P<day>\d{1,2})", "Evil", 0
        )
```

- [ ] **Step 10: Run the tests to verify they fail**

Run: `pytest tests/test_date_rules.py -v`
Expected: FAIL — these 4 tests are new assertions of behavior not yet exercised; run to confirm they fail for the *expected* reason (they should currently pass, since `build_custom_rule` was implemented in Step 7 — if any of these 4 fail unexpectedly, that's real signal to fix before continuing).

- [ ] **Step 11: Run the full file and confirm everything passes**

Run: `pytest tests/test_date_rules.py -v`
Expected: PASS (all 17 tests)

- [ ] **Step 12: Commit**

```bash
git add cost_extractor/date_rules.py tests/test_date_rules.py
git commit -m "feat: add date_rules.py, the spend-date pattern/parser rule engine"
```

---

## Task 2: `pipeline.py` — capture `full_text`/`doc_offset`, spend-date revisions

**Files:**
- Modify: `cost_extractor/pipeline.py:1-11` (imports), `:37-86` (`MatchRecord`), `:88-108` (`DocumentResult`), `:110-174` (`PipelineResult`), `:216-284` (`_process_single_file`)
- Test: `tests/test_spend_dates.py` (new)

**Interfaces:**
- Consumes: `cost_extractor.revisions.Revision`, `record_revision`, `latest_value` (existing, unchanged). `cost_extractor.extractors.base.TextSegment`, `ExtractionResult`, `Status` (existing). `cost_extractor.ingestion.DiscoveredFile` (existing).
- Produces: `MatchRecord.doc_offset: int`, `MatchRecord.spend_date_revisions: list[Revision[Optional[date]]]`, `MatchRecord.spend_date_reviewed: bool` (property), `MatchRecord.effective_spend_date: Optional[date]` (property). `DocumentResult.full_text: str`. `PipelineResult.unreviewed_date_count: int` (property). Used by Task 3 (`gui.py`) and Tasks 6-7 (`report.py`).

- [ ] **Step 1: Write the failing tests for the new `MatchRecord`/`PipelineResult` properties**

Create `tests/test_spend_dates.py`:

```python
"""A human-confirmed spend date, and the rollups that depend on it."""

from datetime import date, datetime, timezone
from decimal import Decimal

from cost_extractor.extractors.base import ExtractionResult, Status, TextSegment
from cost_extractor.ingestion import DiscoveredFile
from cost_extractor.money_parser import default_rules
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult
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


def test_a_never_reviewed_match_is_not_spend_date_reviewed():
    m = _match()

    assert m.spend_date_reviewed is False
    assert m.effective_spend_date is None


def test_confirming_a_date_marks_it_reviewed():
    m = _match()

    record_revision(m.spend_date_revisions, date(2026, 6, 14), now=_NOW)

    assert m.spend_date_reviewed is True
    assert m.effective_spend_date == date(2026, 6, 14)


def test_confirming_no_date_still_marks_it_reviewed():
    # A deliberate "no date applies" decision (App.confirm_no_date) is a
    # completed review, not a missing one -- spend_date_reviewed must be
    # True even though effective_spend_date stays None.
    m = _match()

    record_revision(m.spend_date_revisions, None, now=_NOW, note="confirmed no associated date")

    assert m.spend_date_reviewed is True
    assert m.effective_spend_date is None


def test_a_second_date_confirmation_preserves_the_first_as_history():
    m = _match()
    first = datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc)
    second = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)

    record_revision(m.spend_date_revisions, date(2026, 6, 1), now=first)
    record_revision(m.spend_date_revisions, date(2026, 6, 14), note="fixed", now=second)

    assert [r.value for r in m.spend_date_revisions] == [date(2026, 6, 1), date(2026, 6, 14)]
    assert m.effective_spend_date == date(2026, 6, 14)


def test_unreviewed_date_count_counts_a_never_reviewed_match():
    result = _result([_match()])

    assert result.unreviewed_date_count == 1


def test_unreviewed_date_count_excludes_a_confirmed_date():
    m = _match()
    record_revision(m.spend_date_revisions, date(2026, 6, 14), now=_NOW)
    result = _result([m])

    assert result.unreviewed_date_count == 0


def test_unreviewed_date_count_excludes_a_confirmed_no_date():
    # A deliberate "none" is still a completed review -- must not be
    # double-counted as still-needing-attention.
    m = _match()
    record_revision(m.spend_date_revisions, None, now=_NOW)
    result = _result([m])

    assert result.unreviewed_date_count == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_spend_dates.py -v`
Expected: FAIL with `TypeError` (unexpected keyword `spend_date_revisions` doesn't exist) or `AttributeError` on `spend_date_reviewed`/`effective_spend_date`/`unreviewed_date_count`.

- [ ] **Step 3: Add the new fields/properties to `MatchRecord`, `DocumentResult`, `PipelineResult`**

In `cost_extractor/pipeline.py`, add to the imports (near the top, alongside the existing `from typing import Callable, Optional`):

```python
from datetime import date
```

In `MatchRecord` (after the existing `value_needs_review` property, i.e. after line 85's `return self.confidence < LOW_CONFIDENCE_THRESHOLD`), add:

```python
    # This match's own character offset within its DocumentResult's
    # full_text -- not within its own segment. Needed to compute "nearest
    # date": comparing a match's position to every date candidate found
    # anywhere in the document only makes sense if both are measured in
    # the same coordinate space.
    doc_offset: int = 0
    # Every human decision about this amount's spend date, in order --
    # same append-only discipline as value_revisions. Typed
    # Optional[date]: "no date yet" and "confirmed, no date applies" are
    # both real, expected states.
    spend_date_revisions: list[Revision[Optional[date]]] = field(default_factory=list)

    @property
    def spend_date_reviewed(self) -> bool:
        return bool(self.spend_date_revisions)

    @property
    def effective_spend_date(self) -> Optional[date]:
        return latest_value(self.spend_date_revisions, None)
```

Note: `doc_offset` and the two new dataclass fields above must be added directly after `value_needs_review`'s closing line and *before* the `@dataclass` decorator of `DocumentResult` begins -- they are fields/properties of `MatchRecord`, not a new class.

In `DocumentResult` (after the existing `subtotal: Decimal = Decimal("0")` field, before its `needs_review` property), add:

```python
    # All of this document's segments' text, concatenated at extraction
    # time with a "\n\n" separator between segments (so a date at the
    # very end of one page's text can never appear adjacent to text at
    # the start of the next). Segments are transient -- gone once
    # run_pipeline returns -- so this is captured now for on-demand date
    # suggestion later, in the GUI.
    full_text: str = ""
```

In `PipelineResult` (after the existing `unreviewed_ocr_count` property, i.e. after line 173's closing), add:

```python
    @property
    def unreviewed_date_count(self) -> int:
        """Every match nobody has confirmed -- or explicitly declined --
        a spend date for yet. A confirmed "no date applies" (see
        MatchRecord.spend_date_reviewed) counts as reviewed, not
        unreviewed, the same way an OCR reading a human accepted as-is
        still counts as reviewed for unreviewed_ocr_count."""
        return sum(
            1
            for doc in self.documents
            for m in doc.matches
            if not m.spend_date_reviewed
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_spend_dates.py -v`
Expected: PASS (all 7 tests so far)

- [ ] **Step 5: Run the full existing suite to confirm nothing else broke**

Run: `pytest tests/test_corrections.py tests/test_pipeline.py tests/test_pipeline_e2e.py tests/test_report.py tests/test_report_evidence.py -v`
Expected: PASS -- the two new dataclass fields both have defaults (`doc_offset: int = 0`, `spend_date_revisions: list = field(default_factory=list)`, `full_text: str = ""`), so every existing `MatchRecord(...)`/`DocumentResult(...)` construction in these files keeps compiling unchanged.

- [ ] **Step 6: Write the failing test for `doc_offset`/`full_text` capture**

Append to `tests/test_spend_dates.py`:

```python
def test_process_single_file_computes_doc_offset_and_full_text(monkeypatch):
    from cost_extractor import pipeline as pipeline_module

    segment1_text = "No amounts here."
    segment2_text = "Amount: $100.00 due."
    segment3_text = "Nothing else."
    segments = [
        TextSegment(text=segment1_text, location="page 1"),
        TextSegment(text=segment2_text, location="page 2"),
        TextSegment(text=segment3_text, location="page 3"),
    ]
    fake_extraction = ExtractionResult(status=Status.OK, segments=segments)
    monkeypatch.setattr(
        pipeline_module, "_extract", lambda discovered, ocr_enabled: fake_extraction
    )
    discovered = DiscoveredFile(display_name="fake.docx", suffix=".docx", status=None)

    doc = pipeline_module._process_single_file(discovered, default_rules(), ocr_enabled=True)

    assert doc.full_text == segment1_text + "\n\n" + segment2_text + "\n\n" + segment3_text
    assert len(doc.matches) == 1
    local_start = segment2_text.index("$100.00")
    expected_offset = len(segment1_text) + 2 + local_start  # +2 for the "\n\n" separator
    assert doc.matches[0].doc_offset == expected_offset


def test_a_single_segment_document_has_full_text_equal_to_that_segment(monkeypatch):
    from cost_extractor import pipeline as pipeline_module

    segments = [TextSegment(text="Just one segment, $50.00 total.", location="page 1")]
    fake_extraction = ExtractionResult(status=Status.OK, segments=segments)
    monkeypatch.setattr(
        pipeline_module, "_extract", lambda discovered, ocr_enabled: fake_extraction
    )
    discovered = DiscoveredFile(display_name="fake.docx", suffix=".docx", status=None)

    doc = pipeline_module._process_single_file(discovered, default_rules(), ocr_enabled=True)

    assert doc.full_text == "Just one segment, $50.00 total."
    assert doc.matches[0].doc_offset == doc.full_text.index("$50.00")
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `pytest tests/test_spend_dates.py -v`
Expected: FAIL -- `doc.full_text == ""` (the default) and `doc.matches[0].doc_offset == 0` for both, since `_process_single_file` doesn't compute either yet.

- [ ] **Step 8: Rework `_process_single_file` to compute `doc_offset` and `full_text`**

Replace the body of `_process_single_file` in `cost_extractor/pipeline.py` (the match-building loop and its trailing return, i.e. from `matches: list[MatchRecord] = []` at line 243 through the `return DocumentResult(...)` at line 283) with:

```python
    matches: list[MatchRecord] = []
    doc_cursor = 0
    full_text_parts: list[str] = []
    for segment in extraction.segments:
        found = find_money_matches(segment.text, rules)
        # The match's own character offsets locate it on the page, so no
        # second pass over the text is needed to find it again.
        evidences = [evidence_for_span(segment, m.start, m.end) for m in found]

        # Render the page at most once per segment, and only if something
        # on it actually needs a picture. The image is released with the
        # segment; nothing holds a page beyond this loop.
        page = None
        if segment.page_image is not None and any(e is not None for e in evidences):
            try:
                page = segment.page_image()
            except Exception:  # noqa: BLE001 - a missing crop must not lose the amount
                page = None

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
                    doc_offset=doc_cursor + m.start,
                )
            )

        full_text_parts.append(segment.text)
        # +2 for the "\n\n" separator that will join this segment's text
        # into full_text below.
        doc_cursor += len(segment.text) + 2

    subtotal = sum((m.value for m in matches), Decimal("0"))
    return DocumentResult(
        display_name=discovered.display_name,
        status=extraction.status,
        message=extraction.error_message,
        matches=matches,
        subtotal=subtotal,
        full_text="\n\n".join(full_text_parts),
    )
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `pytest tests/test_spend_dates.py -v`
Expected: PASS (all 9 tests)

- [ ] **Step 10: Run the full existing suite to confirm nothing else broke**

Run: `pytest tests/ -v`
Expected: PASS. (`full_text` is a brand-new field nothing else reads yet; `doc_offset` is populated but nothing else reads it yet either.)

- [ ] **Step 11: Commit**

```bash
git add cost_extractor/pipeline.py tests/test_spend_dates.py
git commit -m "feat: capture full_text/doc_offset per match, spend-date revision fields"
```

---

## Task 3: `gui.py` — App state and the suggest/confirm core

**Files:**
- Modify: `cost_extractor/gui.py:1-53` (imports), `:72-91` (`App.__init__`), `:287-298` (`_run_worker`)
- Test: `tests/test_date_review.py` (new)

**Interfaces:**
- Consumes: `cost_extractor.date_rules.default_rules`, `find_dates`, `nearest_date` (Task 1). `MatchRecord.doc_offset`, `spend_date_revisions`, `spend_date_reviewed`, `effective_spend_date`; `DocumentResult.full_text` (Task 2).
- Produces: `App.date_rules: list`, `App.suggest_spend_date(match) -> Optional[date]`, `App._document_for(match) -> DocumentResult`, `App.confirm_spend_date(match, date_str, note=None) -> Optional[str]`, `App.accept_date_suggestion(match, note=None) -> Optional[str]`, `App.confirm_no_date(match, note=None) -> None`. Used by Task 4 (rule management) and Task 5 (window widgets).

- [ ] **Step 1: Write the failing tests for the suggest/confirm core**

Create `tests/test_date_review.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_date_review.py -v`
Expected: FAIL — `AttributeError: 'App' object has no attribute 'suggest_spend_date'` (and similarly for the other new methods).

- [ ] **Step 3: Add the `date_rules` import and new `App.__init__` state**

In `cost_extractor/gui.py`, add to the imports block (after the existing `from cost_extractor import handwriting` on line 30):

```python
from cost_extractor import date_rules
```

In `App.__init__` (after the existing `self._second_opinions: dict[int, Optional[str]] = {}` on line 87, before `self._build_widgets()`), add:

```python
        self.date_rules: list[date_rules.DateRule] = date_rules.default_rules()
        # Same id(match)-keyed cache shape as _second_opinions -- must be
        # invalidated whenever self.date_rules changes (Task 4).
        self._date_suggestions: dict[int, "Optional[date]"] = {}
        # Built once per run (in _run_worker, below) so a match can find
        # its owning document -- every existing flow iterates
        # "for doc in ... for m in doc.matches" and never needed the
        # reverse direction until now.
        self._match_documents: dict[int, DocumentResult] = {}
        self._custom_date_rule_count = 0
        self._spend_date_window: Optional[tk.Toplevel] = None
        self.spend_date_review_index = 0
```

Add `from datetime import date` to the imports block too (after `from dataclasses import replace` on line 9), so the `Optional[date]` annotation above resolves for anyone inspecting it, and so Steps below (which use `date` in signatures) have it available:

```python
from datetime import date
```

- [ ] **Step 4: Update `_run_worker` to build the match -> document map**

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
        self.last_result = result
        self._match_documents = {
            id(m): doc for doc in result.documents for m in doc.matches
        }
        self._progress_queue.put("done")
```

(The only change from the current code is the new `self._match_documents = {...}` assignment, inserted between `self.last_result = result` and `self._progress_queue.put("done")`.)

- [ ] **Step 5: Run the tests to verify the state-only tests still fail correctly**

Run: `pytest tests/test_date_review.py -v`
Expected: FAIL — still `AttributeError` on `_document_for`/`suggest_spend_date`/etc., since only state and `_run_worker` exist so far, not the methods that use them. (Confirms the tests are exercising real gaps, not accidentally passing from unrelated setup.)

- [ ] **Step 6: Implement `_document_for`, `suggest_spend_date`, `confirm_spend_date`, `accept_date_suggestion`, `confirm_no_date`, and the refresh stub they call**

Add these methods to `App`, placed after `use_second_opinion` (`cost_extractor/gui.py`, after line 238's `return self.apply_correction(match, reading, note=combined_note)`, before `current_review_match`):

```python
    def _document_for(self, match: MatchRecord) -> DocumentResult:
        return self._match_documents[id(match)]

    def suggest_spend_date(self, match: MatchRecord) -> Optional[date]:
        """The nearest date-like text found anywhere in this match's
        document, computed on demand and cached per match. Recomputed
        only when the cache is explicitly invalidated (rule changes --
        see Task 4), never on a timer or a document reload."""
        cached = self._date_suggestions.get(id(match), _UNREAD)
        if cached is not _UNREAD:
            return cached
        document = self._document_for(match)
        candidates = date_rules.find_dates(document.full_text, self.date_rules)
        nearest = date_rules.nearest_date(candidates, match.doc_offset)
        # nearest_date returns the closest DateMatch (or None), not a
        # bare date -- .value is None when the closest date-shaped text
        # nearby failed to parse, and that's still "no suggestion," not
        # license to fall back to a more distant candidate.
        suggestion = nearest.value if nearest is not None else None
        self._date_suggestions[id(match)] = suggestion
        return suggestion

    def confirm_spend_date(
        self, match: MatchRecord, date_str: str, note: Optional[str] = None
    ) -> Optional[str]:
        """Records a human-typed spend date. Parses date_str with the
        same date_rules the suggestion engine uses, so a typed correction
        is held to the same format understanding as a suggestion."""
        found = date_rules.find_dates(date_str, self.date_rules)
        parsed = next((m.value for m in found if m.value is not None), None)
        if parsed is None:
            return "Couldn't recognize that as a date"
        record_revision(match.spend_date_revisions, parsed, note=note)
        self._after_spend_date_change()
        return None

    def accept_date_suggestion(
        self, match: MatchRecord, note: Optional[str] = None
    ) -> Optional[str]:
        suggestion = self.suggest_spend_date(match)
        if suggestion is None:
            return "No date suggestion available for this document."
        cleaned = (note or "").strip() or None
        record_revision(match.spend_date_revisions, suggestion, note=cleaned or "confirmed")
        self._after_spend_date_change()
        return None

    def confirm_no_date(self, match: MatchRecord, note: Optional[str] = None) -> None:
        """The reviewer's explicit "no date applies" decision -- available
        regardless of whether a suggestion exists, distinct from
        accept_date_suggestion's automatic refusal when there is nothing
        to accept. Makes spend_date_reviewed=True with
        effective_spend_date=None a state the app produces on purpose."""
        record_revision(
            match.spend_date_revisions, None, note=note or "confirmed no associated date"
        )
        self._after_spend_date_change()

    def _after_spend_date_change(self) -> None:
        self._refresh_spend_date_widgets()

    def _refresh_spend_date_widgets(self) -> None:
        # Guards exactly like _refresh_review_widgets: safe to call even
        # when the "Confirm Spend Dates..." window (Task 5) isn't open.
        # Task 5 extends this method's body once the window exists to
        # refresh; it does not replace this guard.
        window = self._spend_date_window
        if window is None or not window.winfo_exists():
            return
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_date_review.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 8: Run the full existing suite to confirm nothing else broke**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add cost_extractor/gui.py tests/test_date_review.py
git commit -m "feat: add App.suggest_spend_date/confirm_spend_date/accept_date_suggestion/confirm_no_date"
```

---

## Task 4: `gui.py` — Date Formats rule management

**Files:**
- Modify: `cost_extractor/gui.py` (add methods to `App`, after Task 3's additions)
- Test: `tests/test_date_review.py` (append)

**Interfaces:**
- Consumes: `App.date_rules`, `App._date_suggestions`, `App._custom_date_rule_count` (Task 3 state). `date_rules.build_custom_rule` (Task 1).
- Produces: `App.add_date_rule(pattern_str, label=None) -> Optional[str]`, `App.remove_date_rule(rule_id) -> None`, `App.toggle_date_rule(rule_id, enabled) -> None`. Used by Task 5 (widget event handlers).

- [ ] **Step 1: Write the failing tests for rule management and cache invalidation**

Append to `tests/test_date_review.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_date_review.py -v`
Expected: FAIL — `AttributeError: 'App' object has no attribute 'add_date_rule'` (and similarly for `remove_date_rule`/`toggle_date_rule`).

- [ ] **Step 3: Implement `add_date_rule`, `remove_date_rule`, `toggle_date_rule`, and the checkbox-refresh stub they call**

Add these methods to `App`, placed directly after `confirm_no_date`/`_after_spend_date_change`/`_refresh_spend_date_widgets` (added in Task 3):

```python
    def add_date_rule(self, pattern_str: str, label: Optional[str] = None) -> Optional[str]:
        """Validates and adds a custom date rule. Returns an error
        message on failure (never raises), or None on success."""
        try:
            rule = date_rules.build_custom_rule(
                pattern_str, label or None, self._custom_date_rule_count
            )
        except ValueError as e:
            return str(e)
        self._custom_date_rule_count += 1
        self.date_rules.append(rule)
        self._date_suggestions.clear()
        self._refresh_date_rule_checkboxes()
        return None

    def remove_date_rule(self, rule_id: str) -> None:
        rule = next((r for r in self.date_rules if r.id == rule_id), None)
        if rule is None or rule.built_in:
            return  # built-ins are disableable but not deletable
        self.date_rules = [r for r in self.date_rules if r.id != rule_id]
        self._date_suggestions.clear()
        self._refresh_date_rule_checkboxes()

    def toggle_date_rule(self, rule_id: str, enabled: bool) -> None:
        for r in self.date_rules:
            if r.id == rule_id:
                r.enabled = enabled
        self._date_suggestions.clear()
        self._refresh_date_rule_checkboxes()

    def _refresh_date_rule_checkboxes(self) -> None:
        # Guarded like _refresh_review_button_state: safe to call before
        # the "Date Formats" panel (Task 5) has built its container.
        # Task 5 extends this method's body once the widget exists; it
        # does not replace this guard.
        if not hasattr(self, "_date_rules_container"):
            return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_date_review.py -v`
Expected: PASS (all 19 tests)

- [ ] **Step 5: Run the full existing suite to confirm nothing else broke**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add cost_extractor/gui.py tests/test_date_review.py
git commit -m "feat: add App.add_date_rule/remove_date_rule/toggle_date_rule"
```

---

## Task 5: `gui.py` — the "Date Formats" panel and "Confirm Spend Dates…" window

The spec leaves the window's exact visual design to implementation time
("a third bespoke window... sized at implementation time"), since no
category-window code exists on this branch to compare against (sub-project
2 is unmerged here). This task mirrors the one working precedent that
*does* exist — the OCR review window (`open_review_window`,
`_refresh_review_widgets`, `cost_extractor/gui.py:457-527,574-600`) —
swapping the crop-image display for a text caption (there is no bitmap to
show; the suggestion is text found elsewhere in the document) and
"second opinion" for "date suggestion."

**Files:**
- Modify: `cost_extractor/gui.py:72-92` (`App.__init__`, tail), `:338-455` (`_build_widgets`), `:750-772` (`_refresh_preview_widget`), plus the Task 3/4 stub methods `_refresh_spend_date_widgets`/`_refresh_date_rule_checkboxes`
- Test: `tests/test_date_review.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 3-4 (`App.suggest_spend_date`, `confirm_spend_date`, `accept_date_suggestion`, `confirm_no_date`, `add_date_rule`, `remove_date_rule`, `toggle_date_rule`, `App.date_rules`).
- Produces: `App.spend_date_queue() -> list[MatchRecord]`, `can_confirm_spend_dates() -> bool`, `current_spend_date_match() -> Optional[MatchRecord]`, `next_spend_date_review()`, `previous_spend_date_review()`, `open_spend_date_window() -> Optional[tk.Toplevel]`. Nothing later depends on these beyond the GUI itself.

- [ ] **Step 1: Write the failing tests for the window and panel**

Append to `tests/test_date_review.py`:

```python
def test_the_spend_date_window_opens_and_shows_the_first_match(app):
    _load(app, [_match(raw_text="$100.00")])

    window = app.open_spend_date_window()

    assert window.winfo_exists()
    assert app.current_spend_date_match().raw_text == "$100.00"


def test_opening_the_spend_date_window_twice_reuses_the_same_window(app):
    _load(app, [_match()])

    first = app.open_spend_date_window()
    second = app.open_spend_date_window()

    assert first is second


def test_the_spend_date_queue_includes_every_match_not_just_ocr(app):
    a = _match(raw_text="$100.00")
    b = _match(raw_text="$200.00")
    _load(app, [a, b])

    assert len(app.spend_date_queue()) == 2


def test_moving_through_the_spend_date_queue_changes_the_shown_match(app):
    _load(app, [_match(raw_text="$100.00"), _match(raw_text="$200.00")])
    app.open_spend_date_window()

    first = app.current_spend_date_match()
    app.next_spend_date_review()

    assert app.current_spend_date_match() is not first


def test_the_spend_date_queue_does_not_run_off_the_end(app):
    _load(app, [_match()])
    app.open_spend_date_window()

    app.next_spend_date_review()
    app.next_spend_date_review()

    assert app.current_spend_date_match() is not None


def test_saving_a_spend_date_through_the_window_advances_the_queue(app):
    _load(app, [_match(raw_text="$100.00"), _match(raw_text="$200.00")])
    app.open_spend_date_window()
    first = app.current_spend_date_match()

    app._spend_date_entry.insert(0, "06/14/2026")
    app._on_save_spend_date()

    assert first.effective_spend_date == date(2026, 6, 14)
    assert app.current_spend_date_match() is not first


def test_confirm_no_date_through_the_window_advances_the_queue(app):
    _load(app, [_match(raw_text="$100.00"), _match(raw_text="$200.00")])
    app.open_spend_date_window()
    first = app.current_spend_date_match()

    app._on_confirm_no_date()

    assert first.spend_date_reviewed is True
    assert first.effective_spend_date is None
    assert app.current_spend_date_match() is not first


def test_the_spend_date_button_is_off_until_a_result_is_loaded(app):
    assert "disabled" in app._spend_date_button.state()

    _load(app, [_match()])
    app._refresh_preview_widget()

    assert "disabled" not in app._spend_date_button.state()


def test_the_date_rules_panel_lists_the_built_in_rule(app):
    assert len(app._date_rules_container.winfo_children()) == 1  # numeric_date


def test_adding_a_date_rule_through_the_panel_extends_the_checkbox_list(app):
    app._date_pattern_entry.insert(
        0, r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    )
    app._date_label_entry.insert(0, "ISO")

    app._on_add_date_rule()

    assert len(app._date_rules_container.winfo_children()) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_date_review.py -v`
Expected: FAIL — `AttributeError: 'App' object has no attribute 'spend_date_queue'` / `'_spend_date_button'` / `'_date_rules_container'` and similar, since none of this task's widgets or methods exist yet.

- [ ] **Step 3: Add the "Date Formats" panel to `_build_widgets`**

In `cost_extractor/gui.py`'s `_build_widgets` (after the existing "Money Formats" `rules_frame` block ends — i.e. after line 402's `rules_frame.bind(...)` call — and before `run_frame = ttk.Frame(self.root)` at line 404), add:

```python
        date_rules_frame = ttk.LabelFrame(self.root, text="Date Formats")
        date_rules_frame.pack(fill="x", padx=8, pady=4)
        self._date_rules_container = ttk.Frame(date_rules_frame)
        self._date_rules_container.pack(fill="x")
        self._date_rule_error_label = ttk.Label(date_rules_frame, foreground="red", text="")
        self._date_rule_error_label.pack(fill="x")

        date_custom_frame = ttk.Frame(date_rules_frame)
        date_custom_frame.pack(fill="x", pady=(4, 0))
        ttk.Label(date_custom_frame, text="Custom pattern:").pack(side="left")
        self._date_pattern_entry = ttk.Entry(date_custom_frame)
        self._date_pattern_entry.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Label(date_custom_frame, text="Label:").pack(side="left")
        self._date_label_entry = ttk.Entry(date_custom_frame, width=15)
        self._date_label_entry.pack(side="left", padx=4)
        ttk.Button(date_custom_frame, text="Add", command=self._on_add_date_rule).pack(
            side="left"
        )

        self._date_hint_label = ttk.Label(
            date_rules_frame,
            foreground="gray",
            text=(
                "Regex with required (?P<year>...), (?P<month>...), "
                "(?P<day>...) groups."
            ),
        )
        self._date_hint_label.pack(fill="x", pady=(0, 4))
```

- [ ] **Step 4: Add the "Confirm Spend Dates…" button to `_build_widgets`**

In the same method's `run_frame` block (after the existing `self._review_button.state(["disabled"])` line, i.e. after line 417, before the "Save Report..." button), add:

```python
        self._spend_date_button = ttk.Button(
            run_frame, text="Confirm Spend Dates...", command=self.open_spend_date_window
        )
        self._spend_date_button.pack(side="left", padx=4)
        self._spend_date_button.state(["disabled"])
```

- [ ] **Step 5: Add the queue/navigation methods**

Add these methods to `App`, placed after `toggle_date_rule`/`_refresh_date_rule_checkboxes` (Task 4's additions):

```python
    def spend_date_queue(self) -> list[MatchRecord]:
        """Every match, not just OCR-derived ones -- a spend date applies
        regardless of how the amount was read."""
        if self.last_result is None:
            return []
        return [m for doc in self.last_result.documents for m in doc.matches]

    def can_confirm_spend_dates(self) -> bool:
        return bool(self.spend_date_queue())

    def current_spend_date_match(self) -> Optional[MatchRecord]:
        queue = self.spend_date_queue()
        if not queue:
            return None
        return queue[min(self.spend_date_review_index, len(queue) - 1)]

    def next_spend_date_review(self) -> None:
        queue = self.spend_date_queue()
        if queue:
            self.spend_date_review_index = min(
                self.spend_date_review_index + 1, len(queue) - 1
            )
        self._refresh_spend_date_widgets()

    def previous_spend_date_review(self) -> None:
        self.spend_date_review_index = max(0, self.spend_date_review_index - 1)
        self._refresh_spend_date_widgets()
```

- [ ] **Step 6: Add `open_spend_date_window`**

Add this method to `App`, placed after `open_review_window` (`cost_extractor/gui.py`, after line 527's `return window`):

```python
    def open_spend_date_window(self) -> Optional[tk.Toplevel]:
        """Opens (or raises) the pane for confirming each amount's spend
        date."""
        existing = self._spend_date_window
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_set()
            self._refresh_spend_date_widgets()
            return existing

        if not self.can_confirm_spend_dates():
            return None

        window = tk.Toplevel(self.root)
        window.title("Confirm spend dates")
        self._spend_date_window = window

        ttk.Label(
            window,
            text=(
                'Every amount needs a spend date, or a deliberate "no date '
                'applies."\nThe nearest date found in the document is '
                "suggested below."
            ),
            justify="left",
        ).pack(anchor="w", padx=10, pady=(10, 6))

        self._spend_date_caption = ttk.Label(window, justify="left")
        self._spend_date_caption.pack(anchor="w", padx=10)

        suggestion_row = ttk.Frame(window)
        suggestion_row.pack(fill="x", padx=10, pady=(4, 0))
        self._spend_date_suggestion_label = ttk.Label(suggestion_row, justify="left")
        self._spend_date_suggestion_label.pack(side="left")
        self._spend_date_suggestion_button = ttk.Button(
            suggestion_row, text="Use this", command=self._on_accept_date_suggestion
        )

        entry_row = ttk.Frame(window)
        entry_row.pack(fill="x", padx=10, pady=6)
        ttk.Label(entry_row, text="Date:").pack(side="left")
        self._spend_date_entry = ttk.Entry(entry_row, width=18)
        self._spend_date_entry.pack(side="left", padx=6)
        ttk.Button(entry_row, text="Save date", command=self._on_save_spend_date).pack(
            side="left"
        )
        ttk.Button(
            entry_row, text="No date applies", command=self._on_confirm_no_date
        ).pack(side="left", padx=4)

        note_row = ttk.Frame(window)
        note_row.pack(fill="x", padx=10)
        ttk.Label(note_row, text="Note (optional):").pack(side="left")
        self._spend_date_note_entry = ttk.Entry(note_row, width=40)
        self._spend_date_note_entry.pack(side="left", padx=6, fill="x", expand=True)

        self._spend_date_error = ttk.Label(window, foreground="red")
        self._spend_date_error.pack(anchor="w", padx=10)

        nav = ttk.Frame(window)
        nav.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(nav, text="< Previous", command=self.previous_spend_date_review).pack(
            side="left"
        )
        ttk.Button(nav, text="Next >", command=self.next_spend_date_review).pack(
            side="left", padx=4
        )
        self._spend_date_position = ttk.Label(nav)
        self._spend_date_position.pack(side="left", padx=10)

        self.spend_date_review_index = 0
        self._refresh_spend_date_widgets()
        return window
```

- [ ] **Step 7: Replace the `_refresh_spend_date_widgets` stub with its full body, and add its two helpers**

Replace the Task 3 stub:

```python
    def _refresh_spend_date_widgets(self) -> None:
        # Guards exactly like _refresh_review_widgets: safe to call even
        # when the "Confirm Spend Dates..." window (Task 5) isn't open.
        # Task 5 extends this method's body once the window exists to
        # refresh; it does not replace this guard.
        window = self._spend_date_window
        if window is None or not window.winfo_exists():
            return
```

with:

```python
    def _refresh_spend_date_widgets(self) -> None:
        window = self._spend_date_window
        if window is None or not window.winfo_exists():
            return

        queue = self.spend_date_queue()
        match = self.current_spend_date_match()
        if match is None:
            self._spend_date_caption.config(text="Nothing left to confirm.")
            self._spend_date_position.config(text="")
            return

        self._spend_date_caption.config(
            text=(
                f"{match.display_name} — {match.location}\n"
                f"amount: {match.raw_text}  {self._spend_date_review_summary(match)}"
            )
        )
        self._spend_date_entry.delete(0, tk.END)
        if match.spend_date_reviewed and match.effective_spend_date is not None:
            self._spend_date_entry.insert(0, match.effective_spend_date.isoformat())
        self._spend_date_note_entry.delete(0, tk.END)
        self._spend_date_error.config(text="")
        self._spend_date_position.config(
            text=f"{self.spend_date_review_index + 1} of {len(queue)}"
        )
        self._refresh_spend_date_suggestion_widgets(match)

    def _spend_date_review_summary(self, match: MatchRecord) -> str:
        count = len(match.spend_date_revisions)
        if count == 0:
            return "(not yet reviewed)"
        latest = match.spend_date_revisions[-1]
        when = format_revision_timestamp(latest.at)
        note_suffix = f" ({latest.note})" if latest.note else ""
        value_text = latest.value.isoformat() if latest.value is not None else "no date"
        if count == 1:
            return f"— reviewed once: {value_text} at {when}{note_suffix}"
        return f"— reviewed {count}x, latest: {value_text} at {when}{note_suffix}"

    def _refresh_spend_date_suggestion_widgets(self, match: MatchRecord) -> None:
        suggestion = self.suggest_spend_date(match)
        if suggestion is None:
            self._spend_date_suggestion_label.config(
                text="No date suggestion found in this document."
            )
            self._spend_date_suggestion_button.pack_forget()
            return
        self._spend_date_suggestion_label.config(text=f"Suggested: {suggestion.isoformat()}")
        self._spend_date_suggestion_button.pack(side="left", padx=8)
```

(Use your editor's exact-match replace on the stub block from Task 3 -- both versions share the same first four lines, so match on the full stub including its trailing guard to avoid replacing the wrong occurrence.)

- [ ] **Step 8: Replace the `_refresh_date_rule_checkboxes` stub with its full body, and add `_on_add_date_rule`**

Replace the Task 4 stub:

```python
    def _refresh_date_rule_checkboxes(self) -> None:
        # Guarded like _refresh_review_button_state: safe to call before
        # the "Date Formats" panel (Task 5) has built its container.
        # Task 5 extends this method's body once the widget exists; it
        # does not replace this guard.
        if not hasattr(self, "_date_rules_container"):
            return
```

with:

```python
    def _refresh_date_rule_checkboxes(self) -> None:
        if not hasattr(self, "_date_rules_container"):
            return
        for child in self._date_rules_container.winfo_children():
            child.destroy()

        for rule in self.date_rules:
            row = ttk.Frame(self._date_rules_container)
            row.pack(fill="x")
            var = tk.BooleanVar(value=rule.enabled)
            cb = ttk.Checkbutton(
                row,
                text=rule.label,
                variable=var,
                command=lambda r=rule, v=var: self.toggle_date_rule(r.id, v.get()),
            )
            cb.pack(side="left")
            if not rule.built_in:
                ttk.Button(
                    row,
                    text="×",
                    width=2,
                    command=lambda rid=rule.id: self.remove_date_rule(rid),
                ).pack(side="left")
```

Add `_on_add_date_rule`, placed near the other `_on_*` event handlers (after `_on_add_custom_pattern`, `cost_extractor/gui.py:850-861`):

```python
    def _on_add_date_rule(self) -> None:
        pattern = self._date_pattern_entry.get().strip()
        label = self._date_label_entry.get().strip()
        if not pattern:
            return
        error = self.add_date_rule(pattern, label)
        if error:
            self._date_rule_error_label.config(text=error)
        else:
            self._date_rule_error_label.config(text="")
            self._date_pattern_entry.delete(0, tk.END)
            self._date_label_entry.delete(0, tk.END)
```

- [ ] **Step 9: Add the remaining spend-date window event handlers**

Add these methods to `App`, placed after `_on_use_second_opinion` (`cost_extractor/gui.py:619-626`):

```python
    def _read_spend_date_note_entry(self) -> Optional[str]:
        return self._spend_date_note_entry.get().strip() or None

    def _on_save_spend_date(self) -> None:
        match = self.current_spend_date_match()
        if match is None:
            return
        error = self.confirm_spend_date(
            match, self._spend_date_entry.get(), note=self._read_spend_date_note_entry()
        )
        self._spend_date_error.config(text=error or "")
        if error is None:
            self.next_spend_date_review()

    def _on_accept_date_suggestion(self) -> None:
        match = self.current_spend_date_match()
        if match is None:
            return
        error = self.accept_date_suggestion(match, note=self._read_spend_date_note_entry())
        self._spend_date_error.config(text=error or "")
        if error is None:
            self.next_spend_date_review()

    def _on_confirm_no_date(self) -> None:
        match = self.current_spend_date_match()
        if match is None:
            return
        self.confirm_no_date(match, note=self._read_spend_date_note_entry())
        self._spend_date_error.config(text="")
        self.next_spend_date_review()
```

- [ ] **Step 10: Wire the button's enabled state into `_refresh_preview_widget`, and refresh both new widget groups from `__init__`**

Add a new method, placed after `_refresh_review_button_state` (`cost_extractor/gui.py:718-730`):

```python
    def _refresh_spend_date_button_state(self) -> None:
        """Enables the button once a result exists -- every match needs a
        spend date, so unlike Review Amounts this never depends on
        whether anything was OCR-guessed."""
        if not hasattr(self, "_spend_date_button"):
            return
        if self.can_confirm_spend_dates():
            self._spend_date_button.state(["!disabled"])
        else:
            self._spend_date_button.state(["disabled"])
```

In `_refresh_preview_widget` (`cost_extractor/gui.py:750-757`), add a call to it directly after the existing `self._refresh_review_button_state()` line:

```python
        self._refresh_review_button_state()
        self._refresh_spend_date_button_state()
```

In `App.__init__` (`cost_extractor/gui.py:89-91`), add two calls after the existing `self._refresh_run_button_state()`:

```python
        self._build_widgets()
        self._refresh_rule_checkboxes()
        self._refresh_run_button_state()
        self._refresh_date_rule_checkboxes()
        self._refresh_spend_date_button_state()
```

(Only the last two lines are new; the first three already exist.)

- [ ] **Step 11: Run the tests to verify they pass**

Run: `pytest tests/test_date_review.py -v`
Expected: PASS (all 29 tests)

- [ ] **Step 12: Run the full existing suite to confirm nothing else broke**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 13: Commit**

```bash
git add cost_extractor/gui.py tests/test_date_review.py
git commit -m "feat: add the Date Formats panel and Confirm Spend Dates window"
```

---

## Task 6: `report.py` — `build_workbook`'s new parameter, Details columns, Summary row

**Files:**
- Modify: `cost_extractor/report.py:1-56` (imports, `review_label`), `:21-33` (`_DETAILS_HEADER`), `:105-165` (`build_workbook`)
- Modify (mechanical ripple): `tests/test_report.py` (`test_details_sheet_lists_every_match`)
- Test: `tests/test_report_spend_dates.py` (new)

**Interfaces:**
- Consumes: `date_rules.find_dates`, `nearest_date` (Task 1). `MatchRecord.doc_offset`, `spend_date_reviewed`, `effective_spend_date`; `DocumentResult.full_text`; `PipelineResult.unreviewed_date_count` (Task 2).
- Produces: `build_workbook(result, date_rules=None)` (new optional 2nd parameter — every existing 1-argument call site keeps compiling). `spend_date_label(match, doc, rules) -> str`. Used by Task 7 (Revisions/Spend By Month additions to the same function).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_report_spend_dates.py`:

```python
"""Spend-date columns, Summary row, Revisions Dimension rows, and the
Spend By Month sheet."""

from datetime import date, datetime, timezone
from decimal import Decimal

import openpyxl

from cost_extractor.date_rules import default_rules as default_date_rules
from cost_extractor.extractors.base import Status
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult
from cost_extractor.report import build_workbook, save_workbook
from cost_extractor.revisions import record_revision

_NOW = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)


def _match(value="100.00", doc_offset=0) -> MatchRecord:
    return MatchRecord(
        display_name="scan.pdf",
        location="page 1",
        raw_text=f"${value}",
        rule_id="standard",
        value=Decimal(value),
        doc_offset=doc_offset,
    )


def _result(matches, full_text="") -> PipelineResult:
    return PipelineResult.from_documents(
        [
            DocumentResult(
                display_name="scan.pdf",
                status=Status.OK,
                matches=matches,
                subtotal=sum((m.value for m in matches), Decimal("0")),
                full_text=full_text,
            )
        ]
    )


def _sheet(tmp_path, result, name, rules=None):
    path = tmp_path / "report.xlsx"
    save_workbook(build_workbook(result, rules), path)
    return openpyxl.load_workbook(path)[name]


def test_build_workbook_with_no_date_rules_argument_still_produces_details(tmp_path):
    # The default-None path -- every pre-existing single-argument call
    # site keeps compiling and behaving as before.
    ws = _sheet(tmp_path, _result([_match()]), "Details")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Spend Date")] == "Undated"
    assert row[header.index("Spend Date Review")] == "REVIEW"


def test_details_reports_a_confirmed_date(tmp_path):
    m = _match()
    record_revision(m.spend_date_revisions, date(2026, 6, 14), now=_NOW)
    ws = _sheet(tmp_path, _result([m]), "Details")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Spend Date")] == "2026-06-14"
    assert row[header.index("Spend Date Review")] is None


def test_details_reports_a_confirmed_no_date(tmp_path):
    m = _match()
    record_revision(m.spend_date_revisions, None, now=_NOW)
    ws = _sheet(tmp_path, _result([m]), "Details")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Spend Date")] == "No Date (confirmed)"
    assert row[header.index("Spend Date Review")] is None


def test_details_reports_a_suggested_unconfirmed_date(tmp_path):
    full_text = "Dated 06/14/2026, amount $100.00."
    m = _match(doc_offset=full_text.index("$100.00"))
    ws = _sheet(
        tmp_path, _result([m], full_text=full_text), "Details", rules=default_date_rules()
    )
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Spend Date")] == "2026-06-14 (suggested, unconfirmed)"
    assert row[header.index("Spend Date Review")] == "REVIEW"


def test_summary_reports_dates_not_yet_reviewed(tmp_path):
    reviewed = _match()
    record_revision(reviewed.spend_date_revisions, date(2026, 6, 14), now=_NOW)
    unreviewed = _match()
    ws = _sheet(tmp_path, _result([reviewed, unreviewed]), "Summary")
    labels = {row[0].value: row[3].value for row in ws.iter_rows()}

    assert labels["Dates Not Yet Reviewed"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_report_spend_dates.py -v`
Expected: FAIL — `TypeError: build_workbook() takes 1 positional argument but 2 were given`.

- [ ] **Step 3: Add the `date_rules` import, `build_workbook`'s new parameter, and `spend_date_label`**

In `cost_extractor/report.py`, add to the imports (after the existing `from cost_extractor.revisions import format_revision_timestamp` on line 11):

```python
from cost_extractor import date_rules
```

Add `spend_date_label`, placed after `review_label` (after line 56's `return REVIEW_FLAG if match.value_needs_review else None`, before `def _as_number(value) -> float:`):

```python
def spend_date_label(match, doc: "DocumentResult", rules: list["DateRule"]) -> str:
    if match.spend_date_reviewed:
        # A human can confirm "no date applies" -- that's a completed
        # review, not a missing one, so it gets its own label rather
        # than crashing on effective_spend_date.isoformat() (None has no
        # such method) or reading the same as "nobody has looked yet."
        if match.effective_spend_date is None:
            return "No Date (confirmed)"
        return match.effective_spend_date.isoformat()
    nearest = date_rules.nearest_date(
        date_rules.find_dates(doc.full_text, rules), match.doc_offset
    )
    if nearest is None or nearest.value is None:
        return "Undated"
    return f"{nearest.value.isoformat()} (suggested, unconfirmed)"
```

Change `build_workbook`'s signature (line 105) from:

```python
def build_workbook(result: PipelineResult) -> Workbook:
    wb = Workbook()
```

to:

```python
def build_workbook(
    result: PipelineResult, date_rules: Optional[list["DateRule"]] = None
) -> Workbook:
    active_date_rules = date_rules or []
    wb = Workbook()
```

(The parameter is deliberately named `date_rules`, shadowing the module import of the same name -- exactly for the duration of this function's body. Every other top-level function in this file, including `spend_date_label` above, still sees the un-shadowed module-level `date_rules` import; only code written directly inside `build_workbook` would be affected, and nothing inside it calls `date_rules.` directly -- it only ever passes `active_date_rules` along.)

- [ ] **Step 4: Add `_DETAILS_HEADER`'s two new columns and wire `spend_date_label` into the Details loop**

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
    "Spend Date",
    "Spend Date Review",
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
                    spend_date_label(m, doc, active_date_rules),
                    REVIEW_FLAG if not m.spend_date_reviewed else None,
                ]
            )
```

- [ ] **Step 5: Add the Summary row**

In `build_workbook` (`cost_extractor/report.py:138-146`), after the existing `"Guessed amounts not yet checked"` row append and before `details_ws = wb.create_sheet("Details")`, add:

```python
    summary_ws.append(
        [
            "Dates Not Yet Reviewed",
            None,
            None,
            result.unreviewed_date_count,
            None,
        ]
    )
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `pytest tests/test_report_spend_dates.py -v`
Expected: PASS (all 5 tests)

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
        "Spend Date",
        "Spend Date Review",
    ]

    # These fixtures come from a text layer, so they carry no score and
    # nothing is flagged. Neither match has a spend date confirmed, and
    # _sample_result()'s documents carry no full_text, so nothing can be
    # suggested either -- both read "Undated"/REVIEW.
    row2 = [c.value for c in ws[2]]
    assert row2 == [
        "invoice.docx", "paragraph 1", "$1,234.56", "standard", 1234.56,
        "text", None, None, None, "Undated", "REVIEW",
    ]

    row3 = [c.value for c in ws[3]]
    assert row3 == [
        "invoice.docx", "table 1, row 1, col 2", "($500)", "paren_negative", -500,
        "text", None, None, None, "Undated", "REVIEW",
    ]
```

- [ ] **Step 8: Run the full existing suite to confirm nothing else broke**

Run: `pytest tests/ -v`
Expected: PASS. (`test_report_evidence.py`'s Details tests all use `header.index(...)` lookups, per the file's own established convention, so the two new trailing columns don't disturb them.)

- [ ] **Step 9: Commit**

```bash
git add cost_extractor/report.py tests/test_report_spend_dates.py tests/test_report.py
git commit -m "feat: export Spend Date/Spend Date Review columns and a Summary row"
```

---

## Task 7: `report.py` — Revisions `Dimension` column and the new "Spend By Month" sheet

**Files:**
- Modify: `cost_extractor/report.py:1-11` (imports), `:66-102` (`_REVISIONS_HEADER`, `_revision_rows`), `:105-173` (`build_workbook`)
- Modify (mechanical ripple): `tests/test_report.py` (`test_build_workbook_has_details_and_summary_sheets`), `tests/test_report_evidence.py` (`test_revisions_sheet_header`)
- Test: `tests/test_report_spend_dates.py` (append)

**Interfaces:**
- Consumes: everything from Task 6 (`build_workbook`'s `date_rules` parameter and `active_date_rules`), Task 2 (`MatchRecord.spend_date_revisions`, `effective_spend_date`, `spend_date_reviewed`, `effective_value`).
- Produces: `_spend_date_revision_rows(match) -> list[list]`, `_spend_by_month_rows(result) -> list[list]`. Nothing later depends on these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_report_spend_dates.py`:

```python
def test_revisions_sheet_gets_a_spend_date_dimension_row(tmp_path):
    m = _match()
    record_revision(m.spend_date_revisions, date(2026, 6, 14), note="from invoice", now=_NOW)
    ws = _sheet(tmp_path, _result([m]), "Revisions")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Dimension")] == "Spend Date"
    assert row[header.index("Revised From")] == "Undated"
    assert row[header.index("Revised To")] == "2026-06-14"
    assert row[header.index("Note")] == "from invoice"


def test_a_value_revision_row_reads_value_for_dimension(tmp_path):
    m = _match()
    record_revision(m.value_revisions, Decimal("150.00"), now=_NOW)
    ws = _sheet(tmp_path, _result([m]), "Revisions")
    header = [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]

    assert row[header.index("Dimension")] == "Value"


def test_a_second_spend_date_confirmation_shows_two_revision_rows(tmp_path):
    m = _match()
    first = datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc)
    second = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)
    record_revision(m.spend_date_revisions, date(2026, 6, 1), now=first)
    record_revision(m.spend_date_revisions, date(2026, 6, 14), note="fixed", now=second)
    ws = _sheet(tmp_path, _result([m]), "Revisions")
    header = [c.value for c in ws[1]]
    row1 = [c.value for c in ws[2]]
    row2 = [c.value for c in ws[3]]

    assert row1[header.index("Revised From")] == "Undated"
    assert row1[header.index("Revised To")] == "2026-06-01"
    assert row2[header.index("Revised From")] == "2026-06-01"
    assert row2[header.index("Revised To")] == "2026-06-14"


def test_spend_by_month_sums_matches_into_the_right_month(tmp_path):
    a = _match(value="100.00")
    record_revision(a.spend_date_revisions, date(2026, 6, 14), now=_NOW)
    b = _match(value="50.00")
    record_revision(b.spend_date_revisions, date(2026, 6, 20), now=_NOW)
    c = _match(value="75.00")
    record_revision(c.spend_date_revisions, date(2026, 7, 1), now=_NOW)
    ws = _sheet(tmp_path, _result([a, b, c]), "Spend By Month")
    rows = {row[0]: (row[1], row[2]) for row in ws.iter_rows(min_row=2, values_only=True)}

    assert rows["2026-06"] == (150.0, 2)
    assert rows["2026-07"] == (75.0, 1)


def test_spend_by_month_sorts_chronologically_regardless_of_insertion_order(tmp_path):
    later = _match(value="10.00")
    record_revision(later.spend_date_revisions, date(2026, 8, 1), now=_NOW)
    earlier = _match(value="20.00")
    record_revision(earlier.spend_date_revisions, date(2026, 1, 1), now=_NOW)
    # Constructed in "later, earlier" order to prove the sheet sorts,
    # rather than reflecting incidental dict/insertion order.
    ws = _sheet(tmp_path, _result([later, earlier]), "Spend By Month")
    months = [row[0] for row in ws.iter_rows(min_row=2, max_row=3, values_only=True)]

    assert months == ["2026-01", "2026-08"]


def test_spend_by_month_confirmed_no_date_row(tmp_path):
    m = _match(value="30.00")
    record_revision(m.spend_date_revisions, None, now=_NOW)
    ws = _sheet(tmp_path, _result([m]), "Spend By Month")
    rows = {row[0]: (row[1], row[2]) for row in ws.iter_rows(min_row=2, values_only=True)}

    assert rows["No Date (confirmed)"] == (30.0, 1)
    assert "Not Yet Reviewed" not in rows


def test_spend_by_month_not_yet_reviewed_row(tmp_path):
    m = _match(value="40.00")
    ws = _sheet(tmp_path, _result([m]), "Spend By Month")
    rows = {row[0]: (row[1], row[2]) for row in ws.iter_rows(min_row=2, values_only=True)}

    assert rows["Not Yet Reviewed"] == (40.0, 1)
    assert "No Date (confirmed)" not in rows


def test_a_confirmed_no_date_match_is_distinct_from_an_unreviewed_one(tmp_path):
    # The two-bucket distinction, directly tested: a deliberate "none" and
    # a merely-unreviewed match must never land in the same bucket.
    declined = _match(value="10.00")
    record_revision(declined.spend_date_revisions, None, now=_NOW)
    untouched = _match(value="20.00")
    ws = _sheet(tmp_path, _result([declined, untouched]), "Spend By Month")
    rows = {row[0]: (row[1], row[2]) for row in ws.iter_rows(min_row=2, values_only=True)}

    assert rows["No Date (confirmed)"] == (10.0, 1)
    assert rows["Not Yet Reviewed"] == (20.0, 1)


def test_an_unconfirmed_suggested_date_does_not_reach_a_monthly_total(tmp_path):
    # A rule-suggested-but-unconfirmed date must not put money in a
    # specific month based on a machine guess nobody checked.
    full_text = "Dated 06/14/2026, amount $100.00."
    m = _match(value="100.00", doc_offset=full_text.index("$100.00"))
    ws = _sheet(
        tmp_path,
        _result([m], full_text=full_text),
        "Spend By Month",
        rules=default_date_rules(),
    )
    rows = {row[0]: (row[1], row[2]) for row in ws.iter_rows(min_row=2, values_only=True)}

    assert "2026-06" not in rows
    assert rows["Not Yet Reviewed"] == (100.0, 1)


def test_spend_by_month_buckets_sum_to_the_effective_grand_total(tmp_path):
    dated = _match(value="100.00")
    record_revision(dated.spend_date_revisions, date(2026, 6, 14), now=_NOW)
    declined = _match(value="10.00")
    record_revision(declined.spend_date_revisions, None, now=_NOW)
    untouched = _match(value="20.00")
    result = _result([dated, declined, untouched])
    ws = _sheet(tmp_path, result, "Spend By Month")

    total = sum(row[1] for row in ws.iter_rows(min_row=2, values_only=True))
    assert Decimal(str(total)) == result.effective_grand_total


def test_build_workbook_gains_the_spend_by_month_sheet(tmp_path):
    wb = build_workbook(_result([]))

    assert wb.sheetnames == ["Summary", "Details", "Revisions", "Spend By Month"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_report_spend_dates.py -v`
Expected: FAIL — `KeyError: "Worksheet Spend By Month does not exist."` and `ValueError: 'Dimension' is not in list` for the Revisions tests.

- [ ] **Step 3: Add the `Decimal` import, the `Dimension` column, and `_spend_date_revision_rows`**

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

Change `_revision_rows` (`cost_extractor/report.py:78-102`) to insert `"Value"` as the row's 5th element, matching the header's new position — from:

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
```

- [ ] **Step 4: Wire `_spend_date_revision_rows` into `build_workbook`'s Revisions loop**

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
            for row in _spend_date_revision_rows(m):
                revisions_ws.append(row)

    return wb
```

- [ ] **Step 5: Add `_spend_by_month_rows` and the new sheet, at the end of `build_workbook`**

Add, placed after `_spend_date_revision_rows` (just written) and before `def build_workbook(...)`:

```python
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
```

Change `build_workbook`'s trailing `return wb` (the one just modified in Step 4) to instead build and append the new sheet first:

```python
    spend_by_month_ws = wb.create_sheet("Spend By Month")
    spend_by_month_ws.append(_SPEND_BY_MONTH_HEADER)
    for row in _spend_by_month_rows(result):
        spend_by_month_ws.append(row)

    return wb
```

(i.e. `build_workbook`'s final lines are now: the Revisions loop from Step 4, then this new block, then `return wb` -- "Spend By Month" is created last, after Summary/Details/Revisions, matching the spec's sheet-creation order.)

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `pytest tests/test_report_spend_dates.py -v`
Expected: PASS (all 16 tests)

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
    assert wb.sheetnames == ["Summary", "Details", "Revisions", "Spend By Month"]
```

- [ ] **Step 8: Run the full existing suite to confirm nothing else broke**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add cost_extractor/report.py tests/test_report_spend_dates.py tests/test_report.py tests/test_report_evidence.py
git commit -m "feat: add the Revisions Dimension column and the Spend By Month sheet"
```

---

## Final Verification

- [ ] Run the entire suite once more from a clean tree: `pytest tests/ -v`
- [ ] Manually smoke-test the GUI: run the app, load a document with a date and an amount, open "Confirm Spend Dates…", verify a suggestion appears, save a date, confirm "No date applies" on another match, add a custom date-format pattern, export a report, and open the `.xlsx` to check the Details/Revisions/Spend By Month sheets by eye.
- [ ] Confirm every pre-existing `build_workbook(` call site (grep `build_workbook(` across `cost_extractor/` and `tests/`) still compiles with the new parameters defaulted away.
