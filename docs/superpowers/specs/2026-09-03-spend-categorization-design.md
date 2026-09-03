# Spend categorization — design

Sub-project 2 of 3 (audit foundation → spend categorization → spend over
time). Sub-project 1 (`docs/superpowers/specs/2026-09-03-revision-history-audit-foundation-design.md`)
is merged to `main`. This spec covers categorization only; spend-over-time
gets its own brainstorm/spec/plan cycle once this lands.

## Context

Cost Extractor finds dollar amounts and sums them; it has no notion of
*what* an amount was spent on. This sub-project adds spend categories
(Materials, Labor, Travel, …), auto-suggested from keyword/regex rules the
same way money amounts already are, confirmed by a human before they count
— reusing sub-project 1's `Revision[T]`/`record_revision`/`latest_value`
mechanism exactly as it was built to be reused (confirmed reusable by that
sub-project's final review: `revisions.py` imports nothing money-specific).

Still built to the "produced in discovery" bar established in sub-project
1: every category assignment traceable to a human decision, when it was
made, with the machine's suggestion never silently promoted to fact.

Requirements settled during brainstorming:
- **Every match needs a confirmed category**, not just OCR-derived ones —
  a category total is only as trustworthy as a money total. No toggle
  between "confirmed only" and "rule-suggested included" — both figures
  are always shown side by side, mirroring how `confident_total`/
  `review_total`/`grand_total` already coexist.
- **A small illustrative starter set** of built-in categories, editable
  and removable, not a rigid template.
- **Category rules match against the same line as the amount**, not the
  whole segment (a segment can be a whole page with several line items;
  whole-segment matching would tag every amount on a page with every
  keyword found anywhere on it) and not an arbitrary character window
  (no natural boundary to anchor one).
- **Suggestions are computed on demand, not stored at extraction time** —
  category rules are something a reviewer tunes *during* review (add a
  category, expect it to apply to what's left), unlike money-format rules,
  which are normally set once before a run. Storing the suggestion eagerly
  would go stale the moment a rule changes.
- **A separate review window and rules panel** from the OCR review pane —
  categorization is a different queue (every match, not just OCR-guessed
  ones) and a different kind of decision; overloading one window with two
  unrelated confirm-flows was rejected as confusing.

## Goals

- Every `MatchRecord` can be assigned a category, confirmed by a human,
  recorded as an append-only revision history — reusing `Revision[T]`,
  `record_revision`, `latest_value` unchanged (bound as `Revision[Optional[str]]`
  here, since "no category yet" is a real state, unlike a money value).
- A category suggestion is computed from the current rule set on demand,
  never persisted as fact until confirmed, so editing rules mid-review
  takes effect immediately for anything not yet confirmed.
- The exported report shows, per category, both the confirmed total and
  the rule-suggested-but-unconfirmed total, and how many matches still
  need a category assigned.
- The category-matching mechanism (line-scoped, priority-ordered rules)
  follows the same shape as `money_parser.py`'s rule engine closely enough
  that it's immediately recognizable to someone who already knows that one.

## Non-goals

- Auto-committing a rule-suggested category to the total without
  confirmation — explicitly rejected during brainstorming.
- A GUI toggle between confirmed-only and suggestion-included totals —
  both are always shown; no new persistent app state.
- Multi-category assignment (an amount belongs to more than one category
  at once). One confirmed category per match, matching how money value
  correction already works (one `effective_value`, not several).
- Recording *who* categorized something, or full UI-interaction logging —
  same non-goals as sub-project 1, for the same reasons (single-operator
  app; the note-plus-timestamp bar is what "produced in discovery" needs).
- Sharing the OCR review window/queue with the category review window —
  explicitly rejected; they're separate.

## Design

### `cost_extractor/category_rules.py` (new module)

Mirrors `cost_extractor/money_parser.py`'s shape closely — same
`id`/`label`/`pattern`/`priority`/`enabled`/`built_in` fields as
`MoneyFormatRule` — but simpler: a category rule's job is presence
detection ("does this line mention Labor"), not value extraction, so
there's no `normalizer` callback.

```python
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
    case — editable and removable like everything else here. Fresh
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
    matches. Enabled rules only, lowest `priority` value wins on a tie —
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
    than reimplementing it — same risk, same fix."""
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

`_is_pattern_too_slow` in `money_parser.py` is currently private
(underscore-prefixed) but not otherwise special-cased — importing it
directly is simpler and more honest than duplicating a security-relevant
regex-timing probe. If this reuse turns out awkward in practice, promoting
it to a shared, unprefixed home (e.g. a small `redos_guard.py`) is a fine
fallback; decide at implementation time, not here.

### `MatchRecord` changes (`cost_extractor/pipeline.py`)

Two new fields, added the same way `value_revisions` was:

```python
    # The specific text line this amount was found on — segment.text
    # split on newlines, the line containing this match's character
    # offset. Captured at extraction time because segments are transient
    # (gone once run_pipeline returns); category-rule suggestions need
    # this same line text on demand later, in the GUI, without re-running
    # extraction (which for an OCR'd page would mean re-running OCR).
    line_text: str = ""
    # Every human decision about this amount's category, in order — same
    # append-only discipline as value_revisions, same enforcement (convention
    # via record_revision only). Typed Optional[str], not str: "no category
    # yet" is a real, expected inhabitant of this domain (every match starts
    # uncategorized), unlike a money value, which is never absent.
    category_revisions: list[Revision[Optional[str]]] = field(default_factory=list)

    @property
    def category_reviewed(self) -> bool:
        return bool(self.category_revisions)

    @property
    def effective_category(self) -> Optional[str]:
        """The confirmed category, or None ("Uncategorized") if nobody
        has confirmed one yet. Unlike effective_value, there is no
        machine-extracted fallback — a category is only ever a suggestion
        until a human confirms it, never an extraction."""
        return latest_value(self.category_revisions, None)
```

`line_text` is captured in `_process_single_file`'s match-building loop
(`pipeline.py`, where `bbox`/`crop_png` are already derived per match),
using the same character offsets `find_money_matches` already returns:

```python
def _line_containing(text: str, start: int) -> str:
    """The single line of `text` that character offset `start` falls in."""
    line_start = text.rfind("\n", 0, start) + 1  # 0 if no newline found
    line_end = text.find("\n", start)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end]
```

called as `line_text=_line_containing(segment.text, m.start)` alongside
the existing `MatchRecord(...)` construction.

### GUI changes (`cost_extractor/gui.py`)

**New `App.__init__` state**, listed explicitly (mirroring how
`self.rules`/`self._second_opinions`/`self._custom_rule_count` are already
listed for the money-rule/handwriting mirrors this section follows):
`self.category_rules: list[CategoryRule] = category_rules.default_rules()`,
`self._category_suggestions: dict[int, Optional[str]] = {}` (same
`id(match)`-keyed cache shape as `self._second_opinions`), and
`self._custom_category_rule_count = 0` (mirrors `self._custom_rule_count`).

**Import as `from cost_extractor import category_rules`**, module-qualified
— not `from cost_extractor.category_rules import suggest_category`. Two
reasons: it matches this file's existing convention for exactly this kind
of on-demand-computation module (`from cost_extractor import handwriting`,
called as `handwriting.read_line(...)`/`handwriting.is_available()`), and
it avoids a real name collision — the method being defined below is also
called `suggest_category`, so an unqualified import of the free function
would shadow it inside the class body's own method resolution for anyone
reading casually, and would make `monkeypatch` targets ambiguous (patching
the free function vs. patching the method hits two different things).

**`App.suggest_category(match) -> Optional[str]`** — computed on demand,
cached per match, identical shape to `second_opinion()`:

```python
def suggest_category(self, match: MatchRecord) -> Optional[str]:
    cached = self._category_suggestions.get(id(match), _UNREAD)
    if cached is not _UNREAD:
        return cached
    suggestion = category_rules.suggest_category(match.line_text, self.category_rules)
    self._category_suggestions[id(match)] = suggestion
    return suggestion
```

Unlike `second_opinion`'s cache, this one must be **invalidated when
category rules change** (adding/removing a rule, or toggling one on/off —
there is no *edit* capability, matching the money-rule mirror this follows:
`toggle_rule`/`add_custom_pattern`/`remove_custom_rule` have no edit path
either) — `second_opinion`'s cache never needs this because the
handwriting model itself never changes mid-session. `add_category_rule`/
`remove_category_rule`/`toggle_category_rule` each clear
`self._category_suggestions` entirely after mutating `self.category_rules`,
so the next `suggest_category(match)` call recomputes. This is the
concrete mechanism behind the "tune categories while reviewing" goal.

**`App.confirm_category(match, category, note=None) -> Optional[str]`**
mirrors `apply_correction`: records a human-chosen category (validated
non-empty — an empty/whitespace-only string returns an error message and
records nothing), routed through `record_revision` on `category_revisions`.

**`App.accept_category_suggestion(match, note=None) -> Optional[str]`**
mirrors `accept_reading`, but with one required difference `accept_reading`
doesn't need: `match.value` (what `accept_reading` confirms) always exists
— a money value is never absent. `suggest_category` can legitimately
return `None` (no rule matches the line), so this method needs the same
guard `use_second_opinion` already has for its own "nothing to adopt" case:

```python
def accept_category_suggestion(
    self, match: MatchRecord, note: Optional[str] = None
) -> Optional[str]:
    suggestion = self.suggest_category(match)
    if suggestion is None:
        return "No category suggestion available for this line."
    cleaned = (note or "").strip() or None
    record_revision(match.category_revisions, suggestion, note=cleaned or "confirmed")
    self._after_review_change()
    return None
```

**A new "Categorize Amounts…" window**, structurally parallel to the
existing review window (crop/context display, a queue, next/previous,
confirm/override, optional note) but showing `match.line_text` as plain
text rather than a crop image (there's no OCR uncertainty to visualize
here — the line text is exact, whichever provenance it came from), and
queuing **every match**, not `reviewable_matches()`'s OCR-only set.

**A new "Categories" panel**, structurally parallel to "Money Formats":
checkboxes per category rule (built-in and custom), an "Add category" row
(Name + Pattern), matching `_on_add_custom_pattern`'s validate-and-append
flow via `category_rules.build_custom_rule`.

**On duplicating rather than sharing UI structure with the OCR review
pane**: the new window and panel are real, non-trivial duplication of an
already-shipped pattern (~140 lines of Toplevel/queue/refresh code, ~50
more for the rules panel) — this is a deliberate choice, not an oversight.
Extracting a shared "suggest + confirm + queue" component now would mean
refactoring already-shipped, already-reviewed code from sub-project 1 on
spec, not evidence: one instance (money) plus one new instance (category)
is exactly the point at which "maybe generalize" first becomes visible,
not the point at which duplication has proven costly. Sub-project 3 (spend
date) will need a near-identical third instance — *that* is the point
where two real, working duplicates exist to generalize from, which
produces a better abstraction than guessing at one now from a single
example. If sub-project 3 needs this pattern too, extracting a shared
component becomes that sub-project's first design decision, not this
one's.

### Report changes (`cost_extractor/report.py`)

**`build_workbook`'s signature changes**, and this ripples further than a
one-line mention deserves — enumerated precisely, the same discipline
sub-project 1 applied to its own renames:

```python
def build_workbook(
    result: PipelineResult, category_rules: Optional[list[CategoryRule]] = None
) -> Workbook:
    active_category_rules = category_rules or []
```

**Optional, defaulting to `None`/`[]`** — not required. Grep-confirmed:
`build_workbook(` is called at exactly 13 sites — 1 production
(`cost_extractor/gui.py:325`, inside `export_report`) and 12 test sites
(`tests/test_pipeline_e2e.py:34`; `tests/test_report.py:44,50,82,105`;
`tests/test_report_evidence.py:48,130,175,197,230,263,294`), all passing
a single `PipelineResult` argument today. Making the parameter required
would break all 13 with `TypeError` at call time for no benefit — none of
those 12 test call sites are testing categorization, and a required
parameter would force touching files this sub-project has no reason to
touch. Only `gui.py:325` needs an actual code change, to
`build_workbook(self.last_result, self.category_rules)`; every test site
keeps compiling and behaves exactly as today (see "with no rules
configured" below).

**With no rules configured** (`category_rules=None`/`[]`, i.e. every
existing call site unless/until it opts in): `PipelineResult.uncategorized_count`
(below) is still accurate — it needs no rules, only whether each match is
`category_reviewed`. The Details sheet's `Category`/`Category Review`
columns still appear (schema stays consistent across every export) and
read `"Uncategorized"`/`REVIEW_FLAG` for everything, since nothing can be
suggested with an empty rule set. The new Categories sheet (below) is
still created, with a single `Uncategorized` row summing every match —
this is a coherent degenerate case, not a special-cased branch.

**`PipelineResult` gains `uncategorized_count`**, mirroring
`unreviewed_ocr_count`'s exact shape (`pipeline.py:161-173`) — same
reasoning, same pattern, one line different:

```python
    @property
    def uncategorized_count(self) -> int:
        """Every match nobody has confirmed a category for yet — deliberately
        every provenance and every suggestion state, not just OCR-derived or
        not-yet-suggested ones: "still needs a category assigned" means
        exactly category_reviewed is False, full stop, the same way
        unreviewed_ocr_count doesn't carve out confidently-guessed amounts."""
        return sum(
            1
            for doc in self.documents
            for m in doc.matches
            if not m.category_reviewed
        )
```

Summary gains one row from this, in the same style as
`"Guessed amounts not yet checked"`: `"Amounts not yet categorized"`.

**Details sheet** gains two columns, mirroring the existing
`Value`/`Review` pair's shape exactly rather than inventing a new
convention:

- **`Category`**: the confirmed category if `category_reviewed`, else the
  live rule suggestion (recomputed from `active_category_rules`) marked as
  such, else `"Uncategorized"`. Showing the *suggestion* here (not just
  "Uncategorized") is what makes an unconfirmed row traceable back to
  which category its dollars would land in — without it, a reader could
  not tell which rows make up a category's Unconfirmed total in the
  Categories sheet without re-running the rules themselves.
  ```python
  def category_label(match, rules: list["CategoryRule"]) -> str:
      if match.category_reviewed:
          return match.effective_category
      suggestion = category_rules.suggest_category(match.line_text, rules)
      return f"{suggestion} (suggested, unconfirmed)" if suggestion else "Uncategorized"
  ```
- **`Category Review`**: `REVIEW_FLAG` when `not category_reviewed`, else
  blank — identical shape to the existing `Review` column, so "this still
  needs a human" reads the same way across both dimensions.

`_DETAILS_HEADER` gains both, appended at the end (after `"Read As
Text"`, following the same append-don't-insert convention that column
already established):

```python
_DETAILS_HEADER = [
    "Source File", "Location", "Matched Text", "Rule", "Value", "Source",
    "Confidence", "Review", "Read As Text", "Category", "Category Review",
]
```

and the existing `details_ws.append([...])` row tuple in `build_workbook`
gains the matching two values at the end:

```python
                    m.raw_text if m.value_reviewed else None,
                    category_label(m, active_category_rules),
                    REVIEW_FLAG if not m.category_reviewed else None,
                ]
            )
```

(the first line, `m.raw_text if m.value_reviewed else None`, is the
existing last element of that row — shown for placement, unchanged.)

**New "Categories" sheet** (its own sheet, like "Revisions" — a
per-category breakdown has a different shape than Summary's per-document
rows, and cramming two unrelated row-shapes under one header was the
mockup's actual problem, not merely its formatting). Always created, even
with no category signal at all (mirrors sub-project 1's Revisions sheet
always existing, header-only when there's nothing to show). One row per
`(category, status)` pair, all-numeric cells via the existing `_as_number`
convention — no combined cells, no em-dash:

```
Category         Status         Amount      Match Count
Materials        Confirmed      12340.00    8
Materials        Unconfirmed     2100.00    3
Labor            Confirmed       8900.00    5
Uncategorized    (no signal)      340.00    2
```

Confirmed rows sum `effective_value` for matches whose `effective_category
== category` and `category_reviewed` is true, with `Match Count` counting
them. Unconfirmed rows sum `effective_value` for matches whose
`category_rules.suggest_category(match.line_text, active_category_rules)`
equals that category but `category_reviewed` is false — computed at
export time, not read from anything cached in the GUI (a report can be
generated fresh from a `PipelineResult`, without a live GUI's cache
state). The `Uncategorized` row covers matches with neither a confirmed
category nor a rule suggestion, and is omitted (not shown as a zero row)
when there are none — the sheet's *header* always exists; this one row
within it is conditional.

```python
_CATEGORIES_HEADER = ["Category", "Status", "Amount", "Match Count"]


def _category_summary_rows(
    result: PipelineResult, rules: list["category_rules.CategoryRule"]
) -> list[list]:
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

`build_workbook` creates the sheet unconditionally, immediately after
Details (before Revisions — placement isn't otherwise constrained, this
just keeps the two newest sheets adjacent):

```python
    categories_ws = wb.create_sheet("Categories")
    categories_ws.append(_CATEGORIES_HEADER)
    for row in _category_summary_rows(result, active_category_rules):
        categories_ws.append(row)
```

**New imports for `report.py`**: `from decimal import Decimal` (not
previously imported — `_as_number` took anything with a `float()`
conversion; this module never needed to construct a `Decimal` itself
before now) and `from cost_extractor import category_rules`
(module-qualified, matching `gui.py`'s established convention for this
exact module).

### Revisions sheet changes — category history is not a separate export

Sub-project 1's `Revisions` sheet is money-value-only today. Categorization
reuses the exact same `Revision[T]`/`record_revision` mechanism specifically
so its history gets the exact same audit-trail treatment — a category
confirmed, then re-confirmed differently, needs the same "what happened, in
order, when, with what note" record a money correction already gets. This
is not optional polish: the spec's own stated bar ("every category
assignment traceable to a human decision, when it was made") is not met by
the Details sheet's current-state column alone, the same way `review_label`
alone was never sufficient for money — the full chain lives in Revisions.

**`_REVISIONS_HEADER` gains one column, `Dimension`**, inserted after
`Rule`: `["Source File", "Location", "Matched Text", "Rule", "Dimension",
"Revised From", "Revised To", "Timestamp", "Note"]`. Existing rows get
`Dimension = "Value"`; category rows get `Dimension = "Category"`. Cells in
`Revised From`/`Revised To` hold a number for `"Value"` rows and a string
for `"Category"` rows — openpyxl columns don't require uniform cell types,
and this sets up sub-project 3 to add a `"Spend Date"` dimension to the
same sheet rather than inventing a fourth audit mechanism.

```python
def _category_revision_rows(match) -> list[list]:
    """One row per category-revision event, same chaining rule as
    _revision_rows: "Revised From" is the value immediately before that
    revision — None ("Uncategorized") for the first one, the previous
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

`_revision_rows` (the existing money-value function) gains `"Value"` as a
literal 5th element in its row list, matching the new header position;
`build_workbook`'s Revisions-sheet loop calls both `_revision_rows(m)` and
`_category_revision_rows(m)` for every match, in that order, so a reader
sees a match's value history before its category history when both exist.

**Ripple onto sub-project 1's existing tests**: `tests/test_report.py:46`
(`assert wb.sheetnames == ["Summary", "Details", "Revisions"]`) needs
`"Categories"` added as a 4th sheet. `tests/test_report_evidence.py:178`
(the exact-list header assertion for `_REVISIONS_HEADER`) needs
`"Dimension"` inserted at the right position. Both are mechanical,
name/position-only changes — no test asserting *behavior* (via
`header.index(...)` lookups rather than positional indexing) breaks,
since every existing Revisions-sheet test in that file looks up columns
by name.

## Data flow

Extraction (`pipeline.py`) captures `line_text` per match — nothing else
changes here; existing `value`/`bbox`/`crop_png` capture is untouched. The
GUI computes suggestions on demand from `line_text` + live rules, confirms
into `category_revisions` via `record_revision`, exactly paralleling the
value-correction flow. `report.py` reads `effective_category` (confirmed)
and independently recomputes suggestions (unconfirmed) from whatever
`category_rules` list is passed to `build_workbook` — it does not read
anything the GUI cached (a report can be generated fresh from a
`PipelineResult` without a live GUI's cache state), and if no rules are
passed, every unconfirmed match reads as `Uncategorized` (see "with no
rules configured" above) rather than erroring.

## Error handling

An empty or whitespace-only category name is rejected by
`confirm_category`, same as an unparseable correction — nothing recorded.
An invalid category-rule pattern is rejected by `build_custom_rule`,
same message shape as `money_parser.build_custom_rule`.

## Testing

- New `tests/test_category_rules.py`: `default_rules()` returns fresh
  instances each call (mutation isolation, mirroring
  `test_default_rules_returns_fresh_instances_each_call`);
  `suggest_category` returns `None` on no match, the highest-priority
  label on multiple matches on one line, case-insensitively, and ignores
  a disabled rule that would otherwise match; `build_custom_rule` rejects
  invalid regex and a catastrophic pattern.
- `_line_containing`: tested directly — a match on the first line, a
  match on a middle line, a match on the last line with no trailing
  newline, a single-line segment.
- New GUI tests (mirroring `test_review_pane.py`'s structure):
  - Cache invalidation, with the seeding step made explicit so the test
    can't pass vacuously: call `suggest_category(match)` *before* adding
    a rule (seeding `None` into the cache and asserting it), then add a
    rule that matches `match.line_text`, then call `suggest_category(match)`
    again and assert it now returns the new rule's label. Without the
    seed-and-assert-None step first, a passing test wouldn't prove the
    cache was actually cleared rather than never populated.
  - `confirm_category`/`accept_category_suggestion` append to
    `category_revisions` correctly, including a second-confirmation
    scenario (mirroring sub-project 1's
    `test_a_second_correction_preserves_the_first_as_history`).
  - `accept_category_suggestion` returns the "No category suggestion
    available" error and records nothing when `suggest_category` is `None`
    for that match — the `use_second_opinion`-style guard.
  - `confirm_category` rejects an empty/whitespace-only category name.
- New report tests:
  - Details sheet's `Category`/`Category Review` columns: confirmed,
    suggested-but-unconfirmed (asserting the exact `"{label} (suggested,
    unconfirmed)"` text), and neither (`"Uncategorized"`).
  - The Categories sheet: one row per (category, status), correct
    `Amount`/`Match Count` for a confirmed row and an unconfirmed row in
    the same result; an `Uncategorized` row when at least one match has
    neither; the sheet still exists (header-only) for a result with zero
    matches, mirroring `test_a_match_with_no_revisions_has_no_revisions_sheet_rows`'s
    always-create-the-sheet precedent from sub-project 1.
  - `build_workbook(result)` with no `category_rules` argument at all
    (the default-`None` path) still produces all four sheets without
    error, `uncategorized_count` still accurate, Details' `Category`
    column reading `"Uncategorized"` throughout — proving the 12
    pre-existing, unmodified test call sites keep passing.
  - The Revisions sheet: a category-revision row's `Dimension` column
    reads `"Category"`, a value-revision row's reads `"Value"`, both
    present for a match with both kinds of history, in that order.
  - `PipelineResult.uncategorized_count`: counts a never-reviewed match, a
    confirmed one is excluded, an OCR-derived-but-category-confirmed match
    is excluded too (proving this is genuinely independent of
    `unreviewed_ocr_count`, not accidentally reusing its filter).

## Rollout

Lands on `spend-categorization`, branched from `main` after sub-project 1
merged (PR #1). Own PR against `main` when complete — a different feature
area from sub-project 1's correction-audit trail, not a continuation of
that PR.
