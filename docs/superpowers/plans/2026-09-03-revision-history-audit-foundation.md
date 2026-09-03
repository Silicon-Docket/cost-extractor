# Revision-History Audit Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `MatchRecord.corrected_value` (a single overwritable field with no timestamp) with an append-only revision history, so every human correction to a money amount is preserved — in order, timestamped, with an optional note — instead of the latest one silently erasing the ones before it.

**Architecture:** A small new module (`cost_extractor/revisions.py`) holds a generic `Revision[T]` record plus two free functions (`record_revision`, `latest_value`) and a shared timestamp formatter. `MatchRecord` in `pipeline.py` swaps its single field for a list of these. `gui.py`'s three correction-recording methods (`apply_correction`, `accept_reading`, `use_second_opinion`) append instead of overwrite, each with its own default note. `report.py` gains a dedicated Revisions sheet and a corrected comparison inside `review_label()`.

**Tech Stack:** Python 3.11, dataclasses + `typing.Generic`, `datetime` (UTC, timezone-aware), openpyxl 3.1.5 (pinned — see Global Constraints), Tkinter, pytest.

**Spec:** `docs/superpowers/specs/2026-09-03-revision-history-audit-foundation-design.md` — read it alongside this plan; it explains *why* each decision below was made, including the 17 findings from an automated multi-lens review of the first draft (1 blocking bug, 11 should-fix, 5 minor) that this plan's design already incorporates.

## Global Constraints

- **openpyxl 3.1.5 cannot write a timezone-aware `datetime` into a cell** — it raises `TypeError: Excel does not support timezones in datetimes.` Every timestamp written to a cell, or shown in the GUI, MUST go through `format_revision_timestamp()`. Never write a raw `Revision.at` anywhere.
- `Revision` is a **frozen** dataclass. `value_revisions` is a plain `list`, made append-only **by convention**: the only sanctioned way to add to it is `record_revision()`. Nothing should call `.append()`/`.clear()`/reassign it directly outside that function.
- **Property naming:** money-value-specific properties on `MatchRecord` are prefixed `value_` (`value_reviewed`, `value_needs_review`) so a later sub-project's category/date properties don't collide in meaning. `DocumentResult.needs_review` stays **unprefixed** — it's a dimension-agnostic rollup ("does anything on this document need a look"), which is a different kind of name than a single match's per-dimension state. Its implementation reads `m.value_needs_review`, but its own name doesn't change.
- **Default notes** (only when the caller passes `note=None` or blank): `apply_correction` → no default (stays `None`); `accept_reading` → `"confirmed"`; `use_second_opinion` → `"adopted handwriting model's second opinion"`.
- **Timestamp format**, everywhere: `%Y-%m-%d %H:%M UTC` (minute precision, always UTC, no seconds) — e.g. `2026-09-03 10:22 UTC`.
- **Revision value display convention:** a bare `Decimal` is shown without a `$` prefix, matching the existing convention elsewhere in this codebase (the review entry field, the preview table's Value column) — `raw_text` carries the `$` because it's the literally-matched document text; a `Revision.value` does not. (The spec's illustrative caption example used `$940.00` for readability; this plan follows the codebase's actual, established convention instead — see Task 4.)
- Lands as commits on the existing `ocr-review-pane` branch (open PR #1) — not a new branch or PR.

---

## Task 1: `cost_extractor/revisions.py` — the audit mechanism

**Files:**
- Create: `cost_extractor/revisions.py`
- Test: `tests/test_revisions.py`

**Interfaces:**
- Produces: `Revision` (frozen dataclass: `value: T`, `at: datetime`, `note: Optional[str] = None`), `record_revision(revisions: list[Revision[T]], value: T, note: Optional[str] = None, now: Optional[datetime] = None) -> None`, `latest_value(revisions: list[Revision[T]], original: T) -> T`, `format_revision_timestamp(at: datetime) -> str`. Every later task imports from here.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_revisions.py`:

```python
"""revisions.py: the append-only history behind every human correction.

Pure logic, no Decimal/GUI/report dependency beyond what's needed to
exercise it — this is the shared mechanism a future category/spend-date
sub-project will also import.
"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from cost_extractor.revisions import (
    Revision,
    format_revision_timestamp,
    latest_value,
    record_revision,
)


def test_record_revision_appends_in_order():
    revisions: list[Revision[Decimal]] = []

    record_revision(
        revisions, Decimal("900.00"),
        now=datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc),
    )
    record_revision(
        revisions, Decimal("940.00"),
        now=datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc),
    )

    assert [r.value for r in revisions] == [Decimal("900.00"), Decimal("940.00")]


def test_record_revision_stores_the_given_note():
    revisions: list[Revision[Decimal]] = []

    record_revision(
        revisions, Decimal("940.00"), note="fixed typo",
        now=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )

    assert revisions[0].note == "fixed typo"


def test_record_revision_defaults_note_to_none():
    revisions: list[Revision[Decimal]] = []

    record_revision(revisions, Decimal("940.00"), now=datetime(2026, 9, 3, tzinfo=timezone.utc))

    assert revisions[0].note is None


def test_record_revision_uses_the_system_clock_when_now_is_not_given():
    # Not asserting an exact value (that would be flaky) -- just that a
    # real, timezone-aware UTC timestamp was stamped without an injected one.
    revisions: list[Revision[Decimal]] = []

    record_revision(revisions, Decimal("940.00"))

    assert revisions[0].at.tzinfo is not None
    assert revisions[0].at.utcoffset().total_seconds() == 0


def test_latest_value_returns_original_on_empty_list():
    assert latest_value([], Decimal("440.00")) == Decimal("440.00")


def test_latest_value_returns_the_newest_entry():
    revisions = [
        Revision(value=Decimal("900.00"), at=datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc)),
        Revision(value=Decimal("940.00"), at=datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)),
    ]

    assert latest_value(revisions, Decimal("440.00")) == Decimal("940.00")


def test_multiple_revisions_preserve_their_own_timestamps_and_notes():
    revisions: list[Revision[Decimal]] = []

    record_revision(
        revisions, Decimal("900.00"),
        now=datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc),
    )
    record_revision(
        revisions, Decimal("940.00"), note="fixed typo",
        now=datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc),
    )

    assert revisions[0].at == datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc)
    assert revisions[0].note is None
    assert revisions[1].at == datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)
    assert revisions[1].note == "fixed typo"


def test_revision_is_immutable():
    revision = Revision(value=Decimal("940.00"), at=datetime(2026, 9, 3, tzinfo=timezone.utc))

    with pytest.raises(FrozenInstanceError):
        revision.value = Decimal("1.00")


def test_format_revision_timestamp_produces_the_exact_format():
    at = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)

    assert format_revision_timestamp(at) == "2026-09-03 10:22 UTC"


def test_format_revision_timestamp_pads_single_digit_hours_and_minutes():
    at = datetime(2026, 1, 5, 9, 5, tzinfo=timezone.utc)

    assert format_revision_timestamp(at) == "2026-01-05 09:05 UTC"


def test_format_revision_timestamp_does_not_crash_openpyxl(tmp_path):
    # The fix for the bug the automated spec review caught: proves the
    # *formatted string*, not the raw tz-aware datetime, is what's safe
    # to write into a cell and save.
    import openpyxl

    at = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)
    wb = openpyxl.Workbook()
    wb.active.append([format_revision_timestamp(at)])

    wb.save(tmp_path / "test.xlsx")  # must not raise


def test_a_raw_timezone_aware_datetime_would_crash_the_save(tmp_path):
    # Documents WHY format_revision_timestamp exists: this is what happens
    # if report.py is ever "simplified" back to writing Revision.at
    # directly into a cell. A regression guard, not a test of our own code.
    import openpyxl

    at = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)
    wb = openpyxl.Workbook()
    wb.active.append([at])

    with pytest.raises(TypeError, match="timezone"):
        wb.save(tmp_path / "test.xlsx")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_revisions.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cost_extractor.revisions'`

- [ ] **Step 3: Write the implementation**

Create `cost_extractor/revisions.py`:

```python
"""The append-only history behind every human correction.

A `MatchRecord`'s money value used to carry a single `corrected_value`
with no timestamp, overwritten by a second correction. This module is
what replaces that: every change is appended, never overwritten, so
correcting an amount twice (e.g. fixing your own typo) keeps both
corrections and both timestamps, not just the latest.

Generic so a future sub-project (spend categorization, spend date) can
reuse this exact mechanism instead of inventing its own audit shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Revision(Generic[T]):
    """One recorded change to a value, in order.

    Immutable and append-only by convention: callers add a new Revision to
    a list rather than mutating an existing one, so a corrected reading
    never erases the reading it replaced.
    """
    value: T
    at: datetime  # UTC, timezone-aware — unambiguous across machines/timezones
    note: Optional[str] = None


def record_revision(
    revisions: list["Revision[T]"],
    value: T,
    note: Optional[str] = None,
    now: Optional[datetime] = None,
) -> None:
    """Appends a new revision. `now` is injectable so callers (tests, and
    anything that wants a consistent timestamp across a batch) don't
    depend on the system clock at call time."""
    revisions.append(
        Revision(value=value, at=now or datetime.now(timezone.utc), note=note)
    )


def latest_value(revisions: list["Revision[T]"], original: T) -> T:
    """The most recent revision's value, or `original` if nothing has
    been recorded yet. `original` is never itself a Revision — it's the
    machine reading (OCR text, rule match) that revisions supersede."""
    return revisions[-1].value if revisions else original


def format_revision_timestamp(at: datetime) -> str:
    """The one rendering of a revision's timestamp, shared by the GUI
    caption and the exported Revisions sheet so they can never drift.

    Also the fix for a real bug: `Revision.at` is deliberately
    timezone-aware UTC (unambiguous across machines/timezones), but
    openpyxl raises `TypeError: Excel does not support timezones in
    datetimes` if a tz-aware `datetime` is written directly into a cell
    (verified against openpyxl 3.1.5, the pinned version). Every place
    that surfaces a timestamp — GUI text or an exported cell — uses this
    formatted string, never the raw `datetime`.
    """
    return at.strftime("%Y-%m-%d %H:%M UTC")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_revisions.py -q`
Expected: PASS — 12 passed

- [ ] **Step 5: Commit**

```bash
git add cost_extractor/revisions.py tests/test_revisions.py
git commit -m "Add the append-only revision-history mechanism

Revision[T] + record_revision + latest_value + format_revision_timestamp.
Generic so a future category/spend-date sub-project reuses this instead
of inventing its own audit shape. format_revision_timestamp exists
because openpyxl 3.1.5 crashes writing a tz-aware datetime into a cell —
verified both ways (crash with a raw datetime, no crash with the
formatted string)."
```

---

## Task 2: Reshape `MatchRecord` onto `value_revisions`

**Files:**
- Modify: `cost_extractor/pipeline.py:1-25` (imports), `:36-82` (`MatchRecord`), `:93-95` (`DocumentResult.needs_review`), `:123-133` (`PipelineResult.review_total`), `:157-170` (`PipelineResult.unreviewed_ocr_count`)
- Modify: `tests/test_match_evidence.py` (5 references)
- Modify: `tests/test_corrections.py` (full rewrite of correction call sites)

**Interfaces:**
- Consumes: `Revision`, `record_revision`, `latest_value` from `cost_extractor.revisions` (Task 1).
- Produces: `MatchRecord.value_revisions: list[Revision[Decimal]]`, `MatchRecord.value_reviewed: bool`, `MatchRecord.value_needs_review: bool`, `MatchRecord.effective_value: Decimal` (unchanged signature, new implementation). `MatchRecord.corrected_value` no longer exists — any later task/file that still reads it will fail loudly (`AttributeError`), which is what surfaces the remaining call sites fixed in Tasks 3–5.

- [ ] **Step 1: Update `test_match_evidence.py`**

In `tests/test_match_evidence.py`, five `MatchRecord`-level `.needs_review` references become `.value_needs_review`. `DocumentResult`-level `.needs_review` (the `doc.needs_review` calls) stay unchanged — only the money-value dimension is renamed, not the document-level rollup.

Change:
```python
    assert match.needs_review is False
```
(the one directly after `assert match.bbox is None`, and the one directly after `assert match.confidence >= LOW_CONFIDENCE_THRESHOLD`) to:
```python
    assert match.value_needs_review is False
```

Change:
```python
    assert _match("10.00", confidence=LOW_CONFIDENCE_THRESHOLD).needs_review is False
```
to:
```python
    assert _match("10.00", confidence=LOW_CONFIDENCE_THRESHOLD).value_needs_review is False
```

Leave `doc.needs_review is True` and `doc.needs_review is False` (the two `_document(...)` assertions) exactly as they are.

- [ ] **Step 2: Rewrite `test_corrections.py`**

Replace the full contents of `tests/test_corrections.py`:

```python
"""A human correction overrides what OCR guessed, everywhere.

Reviewing a crop is pointless if fixing the number doesn't change the total.
"""

from datetime import datetime, timezone
from decimal import Decimal

from cost_extractor.extractors.base import Status
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult
from cost_extractor.revisions import record_revision

_NOW = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)


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


def _result(matches) -> PipelineResult:
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


def test_an_uncorrected_match_counts_as_read():
    m = _match("340.00", confidence=84.0)

    assert m.effective_value == Decimal("340.00")


def test_a_correction_replaces_the_read_value():
    # The $940 -> $440 case: read confidently, and still wrong.
    m = _match("440.00", confidence=84.0)

    record_revision(m.value_revisions, Decimal("940.00"), now=_NOW)

    assert m.effective_value == Decimal("940.00")
    assert m.value == Decimal("440.00"), "the original reading is kept for the record"


def test_a_correction_moves_the_document_subtotal():
    matches = [_match("440.00", confidence=84.0), _match("100.00")]
    result = _result(matches)
    assert result.documents[0].effective_subtotal == Decimal("540.00")

    record_revision(matches[0].value_revisions, Decimal("940.00"), now=_NOW)

    assert result.documents[0].effective_subtotal == Decimal("1040.00")


def test_a_correction_moves_the_grand_total():
    matches = [_match("440.00", confidence=84.0)]
    result = _result(matches)

    record_revision(matches[0].value_revisions, Decimal("940.00"), now=_NOW)

    assert result.effective_grand_total == Decimal("940.00")


def test_a_reviewed_match_no_longer_needs_review():
    m = _match("40.00", confidence=31.0)
    assert m.value_needs_review is True

    record_revision(m.value_revisions, Decimal("940.00"), now=_NOW)

    assert m.value_needs_review is False


def test_confirming_a_reading_without_changing_it_also_clears_review():
    # Accepting what OCR read is a judgement too; it must not stay flagged.
    m = _match("40.00", confidence=31.0)

    record_revision(m.value_revisions, m.value, now=_NOW)

    assert m.value_needs_review is False
    assert m.effective_value == Decimal("40.00")


def test_a_correction_of_zero_is_honoured_not_treated_as_absent():
    # Deleting a spurious amount OCR invented is a legitimate correction.
    m = _match("5340.00", confidence=84.0)

    record_revision(m.value_revisions, Decimal("0"), now=_NOW)

    assert m.effective_value == Decimal("0")
    assert m.value_needs_review is False


def test_raw_grand_total_still_reports_what_was_read():
    # The unedited figure stays available, so a correction is visibly a
    # correction rather than a silent rewrite of history.
    matches = [_match("440.00", confidence=84.0)]
    result = _result(matches)

    record_revision(matches[0].value_revisions, Decimal("940.00"), now=_NOW)

    assert result.grand_total == Decimal("440.00")
    assert result.effective_grand_total == Decimal("940.00")


def test_the_three_summary_totals_still_add_up_after_a_correction():
    # The Summary sheet prints all three. If Grand Total moves with
    # corrections but Confidently read does not, the report contradicts
    # itself in front of the user.
    matches = [_match("440.00", confidence=84.0), _match("40.00", confidence=31.0)]
    result = _result(matches)

    record_revision(matches[0].value_revisions, Decimal("940.00"), now=_NOW)

    assert (
        result.confident_total + result.review_total == result.effective_grand_total
    )


def test_correcting_an_amount_moves_it_out_of_the_review_total():
    matches = [_match("40.00", confidence=31.0)]
    result = _result(matches)
    assert result.review_total == Decimal("40.00")

    record_revision(matches[0].value_revisions, Decimal("940.00"), now=_NOW)

    assert result.review_total == Decimal("0")
    assert result.confident_total == Decimal("940.00")


def test_a_second_correction_preserves_the_first_as_history():
    # The scenario this sub-project exists to support: fixing your own
    # mistake must not erase that the first correction ever happened.
    m = _match("440.00", confidence=84.0)
    first = datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc)
    second = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)

    record_revision(m.value_revisions, Decimal("900.00"), now=first)
    record_revision(m.value_revisions, Decimal("940.00"), note="fixed typo", now=second)

    assert [r.value for r in m.value_revisions] == [Decimal("900.00"), Decimal("940.00")]
    assert m.value_revisions[0].at == first
    assert m.value_revisions[1].at == second
    assert m.value_revisions[1].note == "fixed typo"
    assert m.effective_value == Decimal("940.00")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_corrections.py tests/test_match_evidence.py -q`
Expected: FAIL — `AttributeError: 'MatchRecord' object has no attribute 'value_revisions'` (and similar for `value_needs_review`)

- [ ] **Step 4: Reshape `MatchRecord` in `pipeline.py`**

In `cost_extractor/pipeline.py`, add to the imports (after the `cost_extractor.money_parser` import):

```python
from cost_extractor.revisions import Revision, latest_value
```

Replace the `corrected_value` field and its three properties (currently lines 61–82: from `# What a human decided...` through the end of `needs_review`) with:

```python
    # Every human decision about this amount's value, in order — never
    # overwritten. The only sanctioned way to add to this is
    # `record_revision`; nothing else should append/clear/reassign it
    # directly (same convention-over-enforcement discipline
    # money_parser.py already uses for MoneyFormatRule.enabled).
    value_revisions: list[Revision[Decimal]] = field(default_factory=list)

    @property
    def value_reviewed(self) -> bool:
        return bool(self.value_revisions)

    @property
    def effective_value(self) -> Decimal:
        """What this amount is worth, preferring a human's reading."""
        return latest_value(self.value_revisions, self.value)

    @property
    def value_needs_review(self) -> bool:
        # Confidence is a weak signal — Tesseract read $940.00 as $440.00 at
        # 84% — so this flags the obviously-doubtful, and a human's decision
        # always outranks it.
        if self.value_reviewed or self.confidence is None:
            return False
        return self.confidence < LOW_CONFIDENCE_THRESHOLD
```

- [ ] **Step 5: Update the three internal call sites in `pipeline.py`**

`DocumentResult.needs_review` (around line 94): change
```python
        return any(m.needs_review for m in self.matches)
```
to
```python
        return any(m.value_needs_review for m in self.matches)
```
(the property name `needs_review` on `DocumentResult` itself does NOT change — see Global Constraints.)

`PipelineResult.review_total` (around line 130): change
```python
                if m.needs_review
```
to
```python
                if m.value_needs_review
```

`PipelineResult.unreviewed_ocr_count` (around line 169): change
```python
            if m.provenance == "ocr" and not m.reviewed
```
to
```python
            if m.provenance == "ocr" and not m.value_reviewed
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_revisions.py tests/test_corrections.py tests/test_match_evidence.py tests/test_pipeline.py tests/test_pipeline_e2e.py -q`
Expected: PASS (Tasks 3–5 haven't run yet, so do NOT run the full suite here — `gui.py` and `report.py` still reference the now-deleted `corrected_value`/`reviewed`/`needs_review` and will fail to import until those tasks land.)

- [ ] **Step 7: Commit**

```bash
git add cost_extractor/pipeline.py tests/test_corrections.py tests/test_match_evidence.py
git commit -m "Reshape MatchRecord onto value_revisions

corrected_value (a single overwritable field, no timestamp) is replaced
by value_revisions: list[Revision[Decimal]] — correcting the same amount
twice now preserves both corrections instead of the second silently
erasing the first. reviewed/needs_review renamed to
value_reviewed/value_needs_review now, before a future category/date
sub-project would make the unprefixed names ambiguous.

gui.py and report.py still reference the old names and won't import
until the next commits — expected, not a regression."
```

---

## Task 3: Correction methods append instead of overwrite, with default notes

**Files:**
- Modify: `cost_extractor/gui.py:30` (import), `:152` (`reviewable_matches`), `:161-213` (`apply_correction`/`accept_reading`/`use_second_opinion`)
- Modify: `tests/test_review_pane.py` (add new tests; the mechanical renames for *existing* tests happen in Task 4, alongside the widget that makes them exercise the real UI path — see that task's Step 1)

**Interfaces:**
- Consumes: `record_revision` from `cost_extractor.revisions` (Task 1); `MatchRecord.value_revisions`/`value_reviewed` (Task 2).
- Produces: `App.apply_correction(match, text, note=None) -> Optional[str]`, `App.accept_reading(match, note=None) -> None`, `App.use_second_opinion(match, note=None) -> Optional[str]` — all three gain an optional trailing `note` parameter; existing callers (Task 4's UI code, and any test not yet passing `note`) keep working unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review_pane.py` (near the other `apply_correction`/`accept_reading`/`use_second_opinion` tests):

```python
def test_apply_correction_with_no_note_stores_none(app):
    m = _match("440.00", confidence=84.0)
    _load(app, [m])

    app.apply_correction(m, "940.00")

    assert m.value_revisions[-1].note is None


def test_apply_correction_with_a_note_stores_it(app):
    m = _match("440.00", confidence=84.0)
    _load(app, [m])

    app.apply_correction(m, "940.00", note="fixed typo")

    assert m.value_revisions[-1].note == "fixed typo"


def test_accept_reading_with_no_note_defaults_to_confirmed(app):
    m = _match("340.00", confidence=84.0)
    _load(app, [m])

    app.accept_reading(m)

    assert m.value_revisions[-1].note == "confirmed"


def test_accept_reading_with_an_explicit_note_uses_it_instead(app):
    m = _match("340.00", confidence=84.0)
    _load(app, [m])

    app.accept_reading(m, note="double-checked against the invoice")

    assert m.value_revisions[-1].note == "double-checked against the invoice"


def test_use_second_opinion_with_no_note_names_the_model_as_the_source(app, monkeypatch):
    _fake_backend(monkeypatch, "$940.00")
    m = _match("440.00", confidence=82.0)
    _load(app, [m])

    app.use_second_opinion(m)

    assert m.value_revisions[-1].note == "adopted handwriting model's second opinion"


def test_use_second_opinion_with_an_explicit_note_uses_it_instead(app, monkeypatch):
    _fake_backend(monkeypatch, "$940.00")
    m = _match("440.00", confidence=82.0)
    _load(app, [m])

    app.use_second_opinion(m, note="cross-checked with the vendor")

    assert m.value_revisions[-1].note == "cross-checked with the vendor"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_pane.py -q -k "no_note or with_a_note or with_an_explicit_note or names_the_model"`
Expected: FAIL — `AttributeError: 'MatchRecord' object has no attribute 'value_revisions'` (gui.py still writes `match.corrected_value`, so `MatchRecord` — reshaped in Task 2 — has no such attribute; the assertions read `value_revisions`, which exists but is never populated by the old gui.py code path)

- [ ] **Step 3: Update `gui.py`**

Add to imports (after the `handwriting` import, before `money_parser`):

```python
from cost_extractor.revisions import record_revision
```

Replace `apply_correction`, `accept_reading`, and `use_second_opinion` (currently lines 161–213):

```python
    def apply_correction(
        self, match: MatchRecord, text: str, note: Optional[str] = None
    ) -> Optional[str]:
        """Records a human's reading. Returns an error message, or None.

        No default note: the Revised-From/To pair in the export already
        shows a change happened, so free text remains the richer channel
        for *why* rather than an auto-label.
        """
        value = parse_amount(text)
        if value is None:
            return "Enter an amount, e.g. 940.00 or ($200.00)"
        record_revision(match.value_revisions, value, note=note)
        self._after_review_change()
        return None

    def accept_reading(self, match: MatchRecord, note: Optional[str] = None) -> None:
        """Confirms OCR got it right. Still a decision, so still recorded.

        Defaults the note to "confirmed" when left blank: this is the one
        case where the value doesn't change, so the note is the only
        signal that a human deliberately reviewed it rather than it
        happening to match by coincidence.
        """
        record_revision(match.value_revisions, match.value, note=note or "confirmed")
        self._after_review_change()

    def second_opinion(self, match: MatchRecord) -> Optional[str]:
        """What the optional handwriting model makes of the same crop.

        Absent in every packaged build unless a model has been vendored
        deliberately. Never a value on its own — it is shown next to the
        primary reading so a person can weigh the two.
        """
        if not match.crop_png or not handwriting.is_available():
            return None
        cached = self._second_opinions.get(id(match), _UNREAD)
        if cached is not _UNREAD:
            return cached
        try:
            reading = handwriting.read_line(Image.open(io.BytesIO(match.crop_png)))
        except Exception:  # noqa: BLE001 - a second opinion is never worth a crash
            reading = None
        self._second_opinions[id(match)] = reading
        return reading

    def second_opinion_disagrees(self, match: MatchRecord) -> bool:
        """Whether the two engines read different numbers.

        Worth more than either confidence score: Tesseract read $940.00 as
        $440.00 at 82%, which no threshold catches, but a second engine
        reading it differently would have.
        """
        return handwriting.disagrees(match.raw_text, self.second_opinion(match))

    def use_second_opinion(
        self, match: MatchRecord, note: Optional[str] = None
    ) -> Optional[str]:
        """Adopts the model's reading, as a human decision.

        Routed through the same parsing and recording as a typed
        correction, so a suggestion can never slip into the totals without
        someone choosing it. Defaults the note to name the model as the
        source when left blank: "typed by the human" vs. "the human
        accepted the model's suggestion" is a real provenance distinction
        neither the value nor the Revised-From/To pair shows on its own.
        """
        reading = self.second_opinion(match)
        if not reading:
            return "No second reading available for this amount."
        return self.apply_correction(
            match, reading, note=note or "adopted handwriting model's second opinion"
        )
```

(This replaces `second_opinion`/`second_opinion_disagrees` verbatim — they're unchanged — but they're included above because they sit between the two methods that *do* change, so the block is contiguous and unambiguous to copy.)

In `reviewable_matches` (around line 152), change:
```python
            if m.provenance == "ocr" and not (pending_only and m.reviewed)
```
to:
```python
            if m.provenance == "ocr" and not (pending_only and m.value_reviewed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_pane.py -q -k "no_note or with_a_note or with_an_explicit_note or names_the_model"`
Expected: PASS — 6 passed

(The rest of `test_review_pane.py` still fails at this point — it references `corrected_value`/`reviewed`/`needs_review` directly, fixed in Task 4. Don't run the whole file yet.)

- [ ] **Step 5: Commit**

```bash
git add cost_extractor/gui.py tests/test_review_pane.py
git commit -m "Route corrections through record_revision, with default notes by origin

apply_correction/accept_reading/use_second_opinion append a Revision
instead of overwriting corrected_value. accept_reading defaults its note
to 'confirmed' and use_second_opinion to naming the model, since those
are the two cases where the value alone can't tell a deliberate
confirmation from an independently-typed correction, or a typed number
from an adopted suggestion."
```

---

## Task 4: Review pane note field, revision-aware caption, and mechanical test fixes

**Files:**
- Modify: `cost_extractor/gui.py:472-542` (review window widgets and `_refresh_review_widgets`), `:498-513` and `:561-568` (`_on_save_correction`/`_on_accept_reading`/`_on_use_second_opinion`)
- Modify: `tests/test_review_pane.py` (7 mechanical renames + new tests)

**Interfaces:**
- Consumes: `format_revision_timestamp` from `cost_extractor.revisions` (Task 1); `App.apply_correction`/`accept_reading`/`use_second_opinion` now take `note=` (Task 3).
- Produces: `App._review_note_entry` (a `ttk.Entry`), `App._revision_summary(match) -> str` (the caption's parenthetical, also usable by a future task/test in isolation).

- [ ] **Step 1: Fix the 7 existing mechanical references in `test_review_pane.py`**

These currently read `MatchRecord.corrected_value`/`.reviewed`/`.needs_review`, all removed in Task 2:

In `test_an_unparseable_correction_is_rejected_without_changing_anything`, change:
```python
    assert m.corrected_value is None
```
to:
```python
    assert m.value_revisions == []
```

In `test_an_empty_correction_is_rejected`, change:
```python
    assert m.corrected_value is None
```
to:
```python
    assert m.value_revisions == []
```

In `test_accepting_a_reading_marks_it_reviewed_without_changing_the_value`, change:
```python
    assert m.reviewed is True
    assert m.effective_value == Decimal("340.00")
    assert m.needs_review is False
```
to:
```python
    assert m.value_reviewed is True
    assert m.effective_value == Decimal("340.00")
    assert m.value_needs_review is False
```

In `test_the_second_opinion_never_becomes_a_value_by_itself`, change:
```python
    assert m.corrected_value is None
```
to:
```python
    assert m.value_revisions == []
```

In `test_taking_the_second_opinion_records_it_as_a_human_decision`, change:
```python
    assert m.effective_value == Decimal("940.00")
    assert m.reviewed is True
```
to:
```python
    assert m.effective_value == Decimal("940.00")
    assert m.value_reviewed is True
```

In `test_an_unparseable_second_opinion_cannot_be_taken`, change:
```python
    assert app.use_second_opinion(m)
    assert m.corrected_value is None
```
to:
```python
    assert app.use_second_opinion(m)
    assert m.value_revisions == []
```

- [ ] **Step 2: Write the new failing tests**

Append to `tests/test_review_pane.py`:

```python
def test_caption_shows_not_yet_reviewed_before_any_revision(app):
    m = _match("440.00", confidence=84.0)
    _load(app, [m])
    app.open_review_window()

    assert "not yet reviewed" in app._review_caption.cget("text")


def test_caption_shows_reviewed_once_after_a_single_revision(app):
    m = _match("440.00", confidence=84.0)
    _load(app, [m])
    app.open_review_window()

    app.apply_correction(m, "940.00")

    text = app._review_caption.cget("text")
    assert "reviewed once" in text
    assert "940.00" in text


def test_caption_shows_a_revision_count_after_more_than_one(app):
    m = _match("440.00", confidence=84.0)
    _load(app, [m])
    app.open_review_window()

    app.apply_correction(m, "900.00")
    app.apply_correction(m, "940.00")

    assert "reviewed 2x" in app._review_caption.cget("text")


def test_caption_includes_the_note_when_the_note_field_has_one(app):
    m = _match("440.00", confidence=84.0)
    _load(app, [m])
    app.open_review_window()

    app._review_note_entry.insert(0, "fixed typo")
    app._on_save_correction()

    assert "(fixed typo)" in app._review_caption.cget("text")


def test_caption_omits_the_note_parenthetical_when_none_was_given(app):
    m = _match("440.00", confidence=84.0)
    _load(app, [m])
    app.open_review_window()

    app.apply_correction(m, "940.00")

    text = app._review_caption.cget("text")
    assert "()" not in text
    assert "(None)" not in text


def test_the_note_field_clears_between_matches(app):
    a = _match("440.00", confidence=84.0)
    b = _match("40.00", confidence=31.0)
    _load(app, [a, b])
    app.open_review_window()

    app._review_note_entry.insert(0, "note for a")
    app.next_review()

    assert app._review_note_entry.get() == ""


def test_a_blank_note_field_is_recorded_as_none(app):
    m = _match("440.00", confidence=84.0)
    _load(app, [m])
    app.open_review_window()

    app.apply_correction(m, "940.00")  # note field left blank in the widget flow

    assert m.value_revisions[-1].note is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_pane.py -q`
Expected: FAIL — `AttributeError: 'App' object has no attribute '_review_note_entry'`, plus caption-text assertions failing against the unchanged current caption format.

- [ ] **Step 4: Add the note field to the review window**

In `cost_extractor/gui.py`'s `open_review_window`, insert a new row right after the existing `entry_row` block (after the "Looks right" button's `.pack(...)` call, before `self._review_error = ttk.Label(...)`):

```python
        note_row = ttk.Frame(window)
        note_row.pack(fill="x", padx=10)
        ttk.Label(note_row, text="Note (optional):").pack(side="left")
        self._review_note_entry = ttk.Entry(note_row, width=40)
        self._review_note_entry.pack(side="left", padx=6, fill="x", expand=True)
```

- [ ] **Step 5: Wire the note field into the three action handlers**

Add a helper and use it in all three, replacing `_on_save_correction`, `_on_accept_reading`, and `_on_use_second_opinion`:

```python
    def _read_note_entry(self) -> Optional[str]:
        return self._review_note_entry.get().strip() or None

    def _on_save_correction(self) -> None:
        match = self.current_review_match()
        if match is None:
            return
        error = self.apply_correction(
            match, self._review_entry.get(), note=self._read_note_entry()
        )
        self._review_error.config(text=error or "")
        if error is None:
            self.next_review()

    def _on_accept_reading(self) -> None:
        match = self.current_review_match()
        if match is None:
            return
        self.accept_reading(match, note=self._read_note_entry())
        self._review_error.config(text="")
        self.next_review()
```

```python
    def _on_use_second_opinion(self) -> None:
        match = self.current_review_match()
        if match is None:
            return
        error = self.use_second_opinion(match, note=self._read_note_entry())
        self._review_error.config(text=error or "")
        if error is None:
            self.next_review()
```

- [ ] **Step 6: Rewrite the caption and clear the note field on refresh**

Add this method (near `_refresh_review_widgets`):

```python
    def _revision_summary(self, match: MatchRecord) -> str:
        """The parenthetical after "read as $X" -- confidence, plus once
        reviewed, what changed and when. A bare Decimal is shown without a
        $ prefix, matching how effective_value is shown everywhere else in
        this app (the review entry field, the preview table)."""
        confidence = "unknown" if match.confidence is None else f"{match.confidence:.0f}%"
        count = len(match.value_revisions)
        if count == 0:
            return f"(confidence {confidence}, not yet reviewed)"

        latest = match.value_revisions[-1]
        when = format_revision_timestamp(latest.at)
        note_suffix = f" ({latest.note})" if latest.note else ""
        if count == 1:
            return (
                f"(confidence {confidence}) — reviewed once: "
                f"{latest.value} at {when}{note_suffix}"
            )
        return (
            f"(confidence {confidence}) — reviewed {count}x, latest: "
            f"{latest.value} at {when}{note_suffix}"
        )
```

Replace the caption-building block in `_refresh_review_widgets` (currently):
```python
        confidence = "unknown" if match.confidence is None else f"{match.confidence:.0f}%"
        status = "reviewed" if match.reviewed else "not yet reviewed"
        self._review_caption.config(
            text=(
                f"{match.display_name} — {match.location}\n"
                f"read as {match.raw_text}  (confidence {confidence}, {status})"
            )
        )
        self._review_entry.delete(0, tk.END)
        self._review_entry.insert(0, str(match.effective_value))
```
with:
```python
        self._review_caption.config(
            text=(
                f"{match.display_name} — {match.location}\n"
                f"read as {match.raw_text}  {self._revision_summary(match)}"
            )
        )
        self._review_entry.delete(0, tk.END)
        self._review_entry.insert(0, str(match.effective_value))
        self._review_note_entry.delete(0, tk.END)
```

(The added `self._review_note_entry.delete(0, tk.END)` line clears a leftover note from the previous match — without it, a note typed for one amount would silently attach to the next one navigated to.)

Add the import at the top of `gui.py`, alongside the existing `revisions` import from Task 3:
```python
from cost_extractor.revisions import format_revision_timestamp, record_revision
```
(replacing the single-name import added in Task 3).

- [ ] **Step 7: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_review_pane.py -q`
Expected: PASS — all tests in the file pass

- [ ] **Step 8: Commit**

```bash
git add cost_extractor/gui.py tests/test_review_pane.py
git commit -m "Add a note field and revision-aware caption to the review pane

The caption now says how many times an amount was reviewed and when the
latest change happened, using the same format_revision_timestamp the
export uses. The note field clears between matches so a note typed for
one amount can't leak onto the next."
```

---

## Task 5: `review_label()` and the exported Revisions sheet

**Files:**
- Modify: `cost_extractor/report.py` (imports, `review_label`, `build_workbook`)
- Modify: `tests/test_report_evidence.py` (`_corrected_result` helper + new tests)

**Interfaces:**
- Consumes: `format_revision_timestamp` from `cost_extractor.revisions` (Task 1); `MatchRecord.value_revisions`/`value_reviewed`/`value_needs_review`/`effective_value` (Task 2).
- Produces: `review_label(match) -> Optional[str]` (same signature, corrected comparison); a new "Revisions" sheet in every workbook `build_workbook()` produces, header `Source File, Location, Matched Text, Rule, Revised From, Revised To, Timestamp, Note`.

- [ ] **Step 1: Update imports and the `_corrected_result` helper in `test_report_evidence.py`**

Change the existing top-of-file imports from:
```python
from decimal import Decimal

import openpyxl

from cost_extractor.extractors.base import Status
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult
from cost_extractor.report import build_workbook, save_workbook
```
to:
```python
from datetime import datetime, timezone
from decimal import Decimal

import openpyxl

from cost_extractor.extractors.base import Status
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult
from cost_extractor.report import build_workbook, review_label, save_workbook
from cost_extractor.revisions import record_revision

_FIRST = datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc)
_SECOND = datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc)
```

Change `_corrected_result`:
```python
def _corrected_result() -> PipelineResult:
    read_wrong = _match("440.00", confidence=84.0)
    read_wrong.corrected_value = Decimal("940.00")
    checked = _match("200.00", confidence=95.0)
    checked.corrected_value = Decimal("200.00")
```
to:
```python
def _corrected_result() -> PipelineResult:
    read_wrong = _match("440.00", confidence=84.0)
    record_revision(read_wrong.value_revisions, Decimal("940.00"))
    checked = _match("200.00", confidence=95.0)
    record_revision(checked.value_revisions, Decimal("200.00"))
```
(the rest of the function, and every test that calls `_corrected_result()`/`_corrected_sheet()`, is unchanged — they test behavior through `build_workbook`'s output, not the internal field).

- [ ] **Step 2: Write the new failing tests**

Append to `tests/test_report_evidence.py` (the `_FIRST`/`_SECOND` constants and `review_label`/`record_revision` imports used below were already added to the top of the file in Step 1):

```python
def test_revisions_sheet_header(tmp_path):
    result = PipelineResult.from_documents([])
    path = tmp_path / "empty.xlsx"
    save_workbook(build_workbook(result), path)
    ws = openpyxl.load_workbook(path)["Revisions"]

    assert [c.value for c in ws[1]] == [
        "Source File", "Location", "Matched Text", "Rule",
        "Revised From", "Revised To", "Timestamp", "Note",
    ]


def test_a_second_correction_shows_two_rows_in_the_revisions_sheet(tmp_path):
    m = _match("440.00", confidence=84.0)
    record_revision(m.value_revisions, Decimal("900.00"), now=_FIRST)
    record_revision(m.value_revisions, Decimal("940.00"), note="fixed typo", now=_SECOND)
    result = PipelineResult.from_documents(
        [
            DocumentResult(
                display_name="scan.pdf", status=Status.OK,
                matches=[m], subtotal=Decimal("440.00"),
            )
        ]
    )
    path = tmp_path / "report.xlsx"
    save_workbook(build_workbook(result), path)
    ws = openpyxl.load_workbook(path)["Revisions"]

    header = [c.value for c in ws[1]]
    row1 = [c.value for c in ws[2]]
    row2 = [c.value for c in ws[3]]

    assert row1[header.index("Revised From")] == 440.0
    assert row1[header.index("Revised To")] == 900.0
    assert row1[header.index("Timestamp")] == "2026-09-03 10:14 UTC"
    assert row1[header.index("Note")] is None
    assert row2[header.index("Revised From")] == 900.0
    assert row2[header.index("Revised To")] == 940.0
    assert row2[header.index("Timestamp")] == "2026-09-03 10:22 UTC"
    assert row2[header.index("Note")] == "fixed typo"


def test_revisions_sheet_disambiguates_matches_sharing_a_location(tmp_path):
    # location is coarse by construction (a whole page/paragraph/image);
    # Matched Text + Rule are what tell two matches on the same page apart.
    a = _match("100.00", confidence=90.0)
    b = _match("200.00", confidence=90.0)
    record_revision(a.value_revisions, Decimal("150.00"), now=_FIRST)
    record_revision(b.value_revisions, Decimal("250.00"), now=_FIRST)
    result = PipelineResult.from_documents(
        [
            DocumentResult(
                display_name="scan.pdf", status=Status.OK,
                matches=[a, b], subtotal=Decimal("300.00"),
            )
        ]
    )
    path = tmp_path / "report.xlsx"
    save_workbook(build_workbook(result), path)
    ws = openpyxl.load_workbook(path)["Revisions"]

    header = [c.value for c in ws[1]]
    matched_text = [
        row[header.index("Matched Text")]
        for row in ws.iter_rows(min_row=2, values_only=True)
    ]

    assert matched_text == ["$100.00", "$200.00"]


def test_review_label_reads_checked_when_a_reverted_correction_lands_back_on_the_original():
    # Intentional current-state semantics: the Revisions sheet has the
    # full history; this label answers "does it differ right now".
    m = _match("440.00", confidence=84.0)
    record_revision(m.value_revisions, Decimal("900.00"), now=_FIRST)
    record_revision(m.value_revisions, Decimal("440.00"), now=_SECOND)  # reverted

    assert review_label(m) == "checked"


def test_a_match_with_no_revisions_has_no_revisions_sheet_rows(tmp_path):
    result = PipelineResult.from_documents(
        [
            DocumentResult(
                display_name="scan.pdf", status=Status.OK,
                matches=[_match("100.00")], subtotal=Decimal("100.00"),
            )
        ]
    )
    path = tmp_path / "report.xlsx"
    save_workbook(build_workbook(result), path)
    ws = openpyxl.load_workbook(path)["Revisions"]

    assert ws.max_row == 1  # header only
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_report_evidence.py -q`
Expected: FAIL — `AttributeError: 'MatchRecord' object has no attribute 'corrected_value'` (from the not-yet-updated `review_label`) and `KeyError: "Worksheet Revisions does not exist."` for the new tests.

- [ ] **Step 4: Reshape `review_label` and add the Revisions sheet**

In `cost_extractor/report.py`, add to imports:
```python
from cost_extractor.revisions import format_revision_timestamp
```

Replace `review_label` (currently lines 38–48):
```python
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
```

Add this new function (near `_as_number`):
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
                _as_number(previous),
                _as_number(revision.value),
                format_revision_timestamp(revision.at),
                revision.note,
            ]
        )
        previous = revision.value
    return rows
```

In `build_workbook`, after the `Details` sheet loop (after the closing of the `for doc in result.documents: for m in doc.matches: details_ws.append(...)` block, before `return wb`), add:
```python
    revisions_ws = wb.create_sheet("Revisions")
    revisions_ws.append(_REVISIONS_HEADER)
    for doc in result.documents:
        for m in doc.matches:
            for row in _revision_rows(m):
                revisions_ws.append(row)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_report_evidence.py tests/test_report.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add cost_extractor/report.py tests/test_report_evidence.py
git commit -m "Add the Revisions sheet and fix review_label's comparison

New sheet, one row per revision event: Revised From/To (not 'Original
Reading' — ambiguous once a match has more than one revision), plus
Matched Text/Rule so two matches sharing a coarse Location (a whole
page/paragraph/image) stay distinguishable. review_label now compares
effective_value against value instead of the removed corrected_value."
```

---

## Task 6: Full-suite verification and PR update

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `./.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS, all tests (no skips beyond the pre-existing Tesseract-not-installed skip guard, if applicable on this machine)

- [ ] **Step 2: Verify the CI selftest is unaffected**

Run:
```bash
./.venv/Scripts/python.exe -c "
from pathlib import Path
import subprocess, sys, tempfile
d = Path(tempfile.mkdtemp())
subprocess.run([sys.executable, 'scripts/make_selftest_fixture.py', str(d/'s.pdf')], check=True)
from cost_extractor.main import _run_selftest
print(_run_selftest(d/'s.pdf').read_text())
"
```
Expected: output ends with `grand_total=2345.00` — unchanged from before this plan, confirming `PipelineResult.grand_total`'s meaning wasn't disturbed by this refactor.

- [ ] **Step 3: Prove the blocking bug is actually fixed, end to end**

Run a script that builds a real multi-revision match, exports it, and reopens the file — the literal scenario the automated spec review caught as a crash risk:

```bash
./.venv/Scripts/python.exe -c "
from datetime import datetime, timezone
from decimal import Decimal
import openpyxl
from cost_extractor.extractors.base import Status
from cost_extractor.pipeline import DocumentResult, MatchRecord, PipelineResult
from cost_extractor.report import build_workbook, save_workbook
from cost_extractor.revisions import record_revision

m = MatchRecord(display_name='invoice.pdf', location='page 1', raw_text='\$440.00',
                 rule_id='standard', value=Decimal('440.00'), provenance='ocr', confidence=82.0)
record_revision(m.value_revisions, Decimal('900.00'), now=datetime(2026, 9, 3, 10, 14, tzinfo=timezone.utc))
record_revision(m.value_revisions, Decimal('940.00'), note='fixed typo', now=datetime(2026, 9, 3, 10, 22, tzinfo=timezone.utc))
result = PipelineResult.from_documents([DocumentResult(display_name='invoice.pdf', status=Status.OK, matches=[m], subtotal=Decimal('440.00'))])

save_workbook(build_workbook(result), 'C:/Users/William/AppData/Local/Temp/verify_revisions.xlsx')
ws = openpyxl.load_workbook('C:/Users/William/AppData/Local/Temp/verify_revisions.xlsx')['Revisions']
for row in ws.iter_rows(values_only=True):
    print(row)
"
```
Expected: no exception; prints the header row and two data rows showing `440.0 -> 900.0` then `900.0 -> 940.0`, with formatted (not raw) timestamps.

- [ ] **Step 4: Push to update PR #1**

```bash
git push origin ocr-review-pane
```

Confirm the push succeeded and note the commit range now on the PR. This is a visible action (CI will re-run on the open PR) — if anything in Steps 1–3 didn't pass cleanly, stop and fix it before this step, not after.
