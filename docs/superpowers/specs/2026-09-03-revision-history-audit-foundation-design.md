# Revision-history audit foundation — design

Sub-project 1 of 3 (audit foundation → spend categorization → spend over
time). This spec covers the audit foundation only; categorization and
time-based analysis are deliberately out of scope here and get their own
brainstorm/spec/plan cycles once this lands.

## Context

Cost Extractor is used in a legal context; its output may be produced in
discovery or referenced in filings. The user hasn't settled the exact role
yet ("not sure yet"), so this is built to the "produced in discovery" bar:
every number traceable to its source and to when it changed — not full
testimony-grade reconstruction of every UI interaction, and not *who*
changed it (see Non-goals; this is a single-operator-session app today).

PR #1 (`ocr-review-pane`, open, unmerged) added a review pane where a human
confirms or corrects an OCR-guessed amount. Its `MatchRecord` carries a
single `corrected_value` + implicit correction, with no timestamp and no
record of *who* decided it, and no memory of an intermediate correction if
the same amount is corrected twice (e.g. a reviewer fixes their own typo).
For a tool whose numbers might be challenged, "it was corrected, to this
final value, at some unknown point" is not enough.

Because PR #1 is still open, this lands as additional commits on the same
`ocr-review-pane` branch rather than a new PR against `main` — `main` has
no correction fields at all yet, so there's nothing on `main` to migrate;
the reshaping is entirely within a not-yet-merged branch, which is exactly
where it's cheap.

## Goals

- Every human decision that changes what a `MatchRecord` is worth
  (currently: correcting or confirming an OCR reading) is recorded as an
  appended, timestamped revision — never overwritten, *within the
  lifetime of the in-memory objects from a single pipeline run* (see
  Non-goals re: re-running).
- Multiple revisions to the same match preserve the full sequence, in
  order, each independently timestamped, with an optional human-supplied
  note, and each distinguishable from the next by what it changed the
  value *from* as well as *to*.
- The exported report can show the complete revision history, not just
  the final value, unambiguously attributed to the specific match it
  belongs to.
- The mechanism (`Revision[T]`, `record_revision`, `latest_value`,
  `format_revision_timestamp`) is generic enough that sub-projects 2
  (categorization) and 3 (spend date) reuse it rather than inventing
  their own audit shape.

## Non-goals

- Category or spend-date fields on `MatchRecord`. Nothing populates or
  reads them yet; adding them now would be speculative.
- Recording *who* made a change (multi-user identity). This is a
  single-operator-session app; "when" and "what changed" are addressed
  here, "by whom" is not currently a requirement and isn't designed for.
  (The app is portable and could in principle be copied between
  reviewers on a case, so this is a real limitation, not a hypothetical
  one — it's excluded because it's a materially bigger feature, not
  because it doesn't matter.)
- Full UI-interaction logging (every click, every window opened). Only
  changes that affect a value are recorded.
- Surviving a pipeline re-run. `run_pipeline` builds a fresh
  `PipelineResult` every run and the GUI overwrites `self.last_result`
  unconditionally; re-running (e.g. to add a file) discards every
  in-memory `value_revisions` list built up so far unless a report was
  already exported. This is pre-existing app behavior, not introduced or
  fixed here — flagged so "never overwritten" in Goals isn't read as a
  stronger guarantee than it is.
- Tamper-evidence of the exported `.xlsx` itself. This sub-project
  guarantees the report reflects the true, complete revision history
  *at the moment it's exported*; nothing here (no checksum, signature,
  or sheet protection) detects if the file is edited afterward. Excel
  files are trivially editable by design — closing that gap is a
  distinct, larger problem than an in-app audit trail and is not
  attempted.
- Guarding against a misbehaving system clock. `record_revision` trusts
  `datetime.now(timezone.utc)` (or an injected `now`) at call time; if the
  OS clock is adjusted mid-session, list order and timestamp order could
  disagree. Not defended against — the "produced in discovery" bar this
  sub-project targets doesn't require defending against a corrupted host
  clock, and doing so is disproportionate to the actual risk.
- Type-level enforcement that `value_revisions` is append-only.
  `value_revisions` is a plain `list`; nothing stops other code from
  calling `.clear()`/`.pop()`/reassigning it directly instead of going
  through `record_revision`. This is the same convention-over-enforcement
  discipline `money_parser.py` already uses for `MoneyFormatRule.enabled`
  ("mutated in place... callers must never share a cached/module-level
  list") — accepted elsewhere in this codebase, not a new risk class.

## Design

### `cost_extractor/revisions.py` (new module)

Kept separate from `pipeline.py`, which has already absorbed several
concerns this session (crop capture, confidence thresholds, three
`PipelineResult` totals). This module is small and self-contained, and is
what sub-projects 2/3 import.

```python
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

### `MatchRecord` changes (`cost_extractor/pipeline.py`)

Removed: `corrected_value: Optional[Decimal]` (there is no
`corrected_at` today — `MatchRecord` currently has no timestamp field at
all, which is precisely the gap this sub-project fills).

Added: `value_revisions: list[Revision[Decimal]] = field(default_factory=list)`.
The only sanctioned way to change it is `record_revision`; nothing else in
this codebase or in sub-projects 2/3 should append/clear/reassign it
directly (see Non-goals re: this being convention, not type-enforced).

**Renamed**, not just re-implemented: `reviewed` → `value_reviewed`
(`bool(self.value_revisions)`), `needs_review` → `value_needs_review`
(same confidence-based logic as today, ignored once `value_reviewed`).
Sub-projects 2/3 will need their own `category_reviewed`/
`spend_date_reviewed`-shaped properties; leaving the money-value ones
unprefixed would make `match.reviewed` ambiguous the moment a second
dimension exists. Renaming now, while there are 22 existing call sites
(grep-confirmed: `gui.py`, `pipeline.py`, `report.py`,
`test_corrections.py`, `test_match_evidence.py`, `test_review_pane.py`),
is cheaper than renaming later after sub-project 2 adds more.
`effective_value` keeps its name — it's already unambiguous.

`DocumentResult.effective_subtotal` and `PipelineResult.effective_grand_total`
/`review_total`/`confident_total` are unchanged in *behavior*; they already
go through `effective_value`, so they need no logic changes, only to keep
compiling against the new field shape.

**Default notes by origin.** Three call sites append a revision, and only
one of them (a value that stays the same) is otherwise indistinguishable
between "deliberately confirmed" and "corrected back to the original by
coincidence" — so only that one gets an automatic default:
- `apply_correction(match, text, note=None)`: `note` stored as given
  (blank → `None`). No auto-label — the Revised-From/To pair in the
  export already shows a change happened; free text remains the richer
  channel for *why*.
- `accept_reading(match, note=None)`: if `note` is blank, defaults to
  `"confirmed"`. This is the only case where the value doesn't change, so
  it's the only case where the note is the *sole* signal that a human
  deliberately reviewed it.
- `use_second_opinion(match, note=None)` (already in PR #1, routes into
  `apply_correction`): if `note` is blank, defaults to `"adopted
  handwriting model's second opinion"`. "The human typed this number
  themselves" vs. "the human accepted the model's suggestion" is a real
  provenance distinction neither the value nor the From/To pair can show
  on its own.

### GUI changes (`cost_extractor/gui.py`)

`apply_correction(match, text, note=None)` and `accept_reading(match,
note=None)` call `record_revision(match.value_revisions, ...)` instead of
setting `corrected_value`, applying the default-note-by-origin rule above.

The review pane's entry row gains a second, optional text field for a
note ("why", e.g. "fixed typo") — never required, so the common case
(confirm or correct with no explanation) stays a two-click action exactly
as it is today. A blank field is normalized the same way the codebase
already normalizes an optional label elsewhere (`add_custom_pattern`'s
`label or None`): `note = self._review_note_entry.get().strip() or None`.

The review window's caption, which currently shows `"read as {raw_text}
(confidence {confidence}, {status})"`, changes based on
`len(match.value_revisions)`:

| revisions | caption |
|---|---|
| 0 | `read as $440.00 (confidence 84%, not yet reviewed)` (unchanged) |
| 1 | `read as $440.00 (confidence 84%) — reviewed once: $940.00 at 2026-09-03 10:22 UTC (fixed typo)` |
| N > 1 | `read as $440.00 (confidence 84%) — reviewed 2x, latest: $940.00 at 2026-09-03 10:22 UTC (fixed typo)` |

The timestamp is always `format_revision_timestamp(latest.at)`; the
trailing `(note)` is omitted entirely when the latest revision's note is
`None`, not rendered as `(None)` or `()`.

### Report changes (`cost_extractor/report.py`)

**`review_label()`** (currently `"corrected" if match.corrected_value !=
match.value else "checked"`, read directly by both the Details sheet and
the GUI preview — this is the one production reference to the
old field the earlier draft's grep-count included but didn't explain how
to reshape) becomes, for a reviewed match:
`"corrected" if match.effective_value != match.value else "checked"`.
This is a *current-state* label: a match corrected twice that ends back
at its original value reads `"checked"`, the same as a match nobody ever
touched a second time. That's intentional, not a gap — Details/Summary
answer "does the number differ from the machine reading right now",
the new Revisions sheet (below) answers "what happened, in order", and
those are legitimately different questions. `Details`/`Value` continues
to reflect `effective_value`, and keeps its existing `Read As Text`
column (`match.raw_text`, the original OCR/rule reading), unchanged.

New **Revisions** sheet, one row per revision *event* (one row per
element of `value_revisions`, across every match that has at least one):

`Source File, Location, Matched Text, Rule, Revised From, Revised To, Timestamp, Note`

`Matched Text` and `Rule` are added beyond the two fields the earlier
draft proposed (`Source File`, `Location`) because `location` is coarse
by construction — `image_extractor.py` sets it to the literal string
`"image"` for every match in a file, `pdf_extractor.py` uses `f"page
{i}"` for the whole page, `docx_extractor.py` uses `f"paragraph {i}"` for
the whole paragraph — so a document with more than one dollar amount on
the same page/paragraph/image (an ordinary invoice or table) would have
indistinguishable rows without them. `Details` already uses this same
five-field combination to identify a row; `Revisions` follows suit.

**`Revised From` / `Revised To`**, not "Original Reading" — a name that
turned out to have two valid readings once a match has more than one
revision (the machine's original value, constant across every row for
that match, vs. what that specific revision superseded). `Revised From`
removes the ambiguity: it's the value immediately before *this* row's
revision — `match.value` for a match's first revision, the previous
revision's `.value` for every one after. Reading down a match's rows
reconstructs the chain. Worked example, a match corrected twice:

```
invoice_889.pdf | page 1 | $440.00 | standard | 440.00 | 900.00 | 2026-09-03 10:14 UTC | (blank)
invoice_889.pdf | page 1 | $440.00 | standard | 900.00 | 940.00 | 2026-09-03 10:22 UTC | fixed typo
```

`Timestamp` is `format_revision_timestamp(revision.at)` — a string, not
the raw `datetime` (see the `revisions.py` note above on why a raw
tz-aware `datetime` would crash the save). Rows are ordered the way
`Details` already is — per document (`for doc in result.documents`, the
existing order), then per match within that document, then in revision
order within a match — not globally sorted by timestamp across the whole
batch.

## Data flow

Unchanged for every existing consumer: `effective_value` is still the one
thing totals, the preview table, and the Details sheet read. What's new is
additive — a `Revisions` sheet enumerating the history, and richer text in
the review pane. No caller outside `revisions.py` and the two write sites
(`apply_correction`, `accept_reading`) needs to know revisions are a list
rather than a scalar.

## Error handling

Unchanged. An unparseable correction (`parse_amount` returns `None`)
returns an error message and appends nothing — exactly as today, just
"appends nothing" replaces "sets nothing."

## Testing

- New `tests/test_revisions.py`: `record_revision` appends in order;
  `latest_value` returns `original` on an empty list and the newest
  entry otherwise; multiple revisions are all preserved with their own
  timestamps and notes; the injectable `now` parameter makes timestamps
  assertable without depending on the system clock;
  `format_revision_timestamp` produces the exact `%Y-%m-%d %H:%M UTC`
  string used everywhere else.
- Updated, two overlapping-but-distinct greps against the pre-this-spec
  code, both now in scope (there is no existing `corrected_at` to
  migrate — only `corrected_value`, confirmed by grep; the earlier draft
  of this spec incorrectly implied one existed):
  - `corrected_value` → `value_revisions`: `gui.py` (2), `pipeline.py`
    (3), `report.py` (1), `test_corrections.py` (9),
    `test_review_pane.py` (4), `test_report_evidence.py` (2). 21
    references.
  - `.reviewed`/`.needs_review` → `.value_reviewed`/`.value_needs_review`:
    `gui.py`, `pipeline.py`, `report.py`, `test_corrections.py`,
    `test_match_evidence.py`, `test_review_pane.py`. 22 references
    (files overlap with the list above; exact line-level union to be
    confirmed at implementation time rather than hand-summed here).
- New: correcting the same match twice preserves both revisions, in
  order, each with its own timestamp and correct `Revised From`/`Revised
  To` pairing — the scenario this whole sub-project exists to support.
- New: the exported Revisions sheet has one row per revision, with
  `Matched Text`/`Rule` correctly disambiguating two matches that share
  the same `Source File`/`Location` (the coarse-location scenario
  above), in the batch's document-then-match-then-revision order.
- New: `review_label()`/the Details `Review` column reads `"checked"`
  for a match accepted via `accept_reading` with no value change, and
  for a match corrected twice that lands back on its original value
  (the intentional current-state-only semantics above) — and reads
  `"corrected"` whenever `effective_value != value`.
- New: `accept_reading` with no note defaults to `"confirmed"`;
  `use_second_opinion` with no note defaults to `"adopted handwriting
  model's second opinion"`; `apply_correction` with no note stores
  `None` (no auto-label) — each verified independently, since they're
  three different defaulting rules on the same underlying
  `record_revision` call.

## Rollout

Implementation lands as additional commits on the existing
`ocr-review-pane` branch (PR #1), pushed to update that PR — not a new
branch or PR. `main` has no correction fields yet, so there is nothing on
`main` this needs to migrate.
