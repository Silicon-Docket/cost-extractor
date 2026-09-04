# Spend over time — design

Sub-project 3 of 3 (audit foundation → spend categorization → spend over
time). Sub-project 1 (`2026-09-03-revision-history-audit-foundation-design.md`)
is merged to `main`. Sub-project 2 (`2026-09-03-spend-categorization-design.md`)
is spec'd, not yet implemented or merged. This spec covers spend-over-time
only, and is written to apply cleanly regardless of which of sub-projects 2
and 3 the user chooses to implement/merge first — see Rollout.

## Context

Sub-projects 1 and 2 answer "how much, corrected how" and "what for."
This sub-project answers "when" — associating a spend date with each
amount, confirmed by a human, and rolling that up into an actual
time-series view (Spend By Month), which is the deliverable this whole
sub-project exists to produce.

Still built to the "produced in discovery" bar: every date assignment
traceable to a human decision, when it was made, reusing
`Revision[T]`/`record_revision`/`latest_value` exactly as designed to be
reused (a third instance now, after money value and category).

Requirements settled during brainstorming:
- **Whole-document search, nearest by position** — unlike a category
  keyword (usually on the same line as its amount), an invoice date
  typically appears once, elsewhere on the page, and governs many line
  items below it. Line-scoped search (categorization's approach) would
  leave nearly every amount unsuggested.
- **Search happens on demand against persisted raw text, not a
  precomputed guess** — the same reasoning sub-project 2 established for
  categories applies here, arguably more strongly: date formats vary more
  across document sources than category keywords do, and a format your
  documents use but the built-in rules miss needs to be addable mid-review
  without a full pipeline re-run (OCR included).
- **Built-in date formats: numeric only** — `MM/DD/YYYY` and `MM-DD-YYYY`,
  four-digit year. Not ISO 8601, not written-month formats — narrower than
  originally offered; anything else is a custom pattern, the same escape
  hatch money and category rules already have.
- **US date convention (month before day)** — `03/04/2026` parses as March
  4th, not April 3rd. This is a real, unresolvable ambiguity in the format
  itself, not a bug; a document using day-first dates needs a custom
  pattern (see Non-goals).
- **Spend By Month, not quarter** — one row per calendar month, no
  separate quarterly rollup. Two further buckets, not one: a
  match whose spend date was actively confirmed as "none" is a different
  fact than a match nobody has reviewed yet, so the sheet keeps them
  separate rather than merging both into a single `Undated` row (see
  Design, `_spend_by_month_rows`).

## Goals

- Every `MatchRecord` can be assigned a spend date, confirmed by a human,
  recorded as an append-only revision history — `Revision[Optional[date]]`,
  same mechanism, third reuse.
- A date suggestion is the nearest date-like text found anywhere in the
  matches's own document, computed on demand from the current rule set,
  so a date-format rule added mid-review can find dates the built-in
  rules missed without re-running extraction.
- The exported report gains a genuinely chronological view — Spend By
  Month — distinct from the Revisions sheet's document-then-match-then-
  revision order, which stays as sub-project 1 defined it.
- The date-matching mechanism (whole-document, pattern **and** parser per
  rule) follows `money_parser.py`'s shape where that shape actually fits
  — a date rule needs to both find *and interpret* text, unlike a category
  rule, which only needs to find it.

## Non-goals

- Day-first (DD/MM/YYYY) or two-digit-year date parsing as a built-in
  rule. A document using either needs a custom pattern (see Design,
  `date_rules.build_custom_rule` — fully specified, not deferred: a
  day-first pattern just names its groups `year`/`month`/`day` in a
  different order); the built-in rules assume US convention, four-digit
  years, per the brainstorming answer above.
- A quarterly rollup sheet — monthly only, per the brainstorming answer.
- Auto-committing a suggested date to the time-series without confirmation
  — same non-goal as category, for the same reason.
- Recording *who* assigned a date, or full UI-interaction logging — same
  non-goals as sub-projects 1 and 2, same reasoning.
- Sharing the category review window/queue with a new date review window
  — see Design, "On a third confirm-workflow instance," which addresses
  this directly rather than silently repeating sub-project 2's YAGNI
  argument unchanged.

## Design

### `cost_extractor/date_rules.py` (new module)

Closer to `money_parser.py`'s shape (merged, in the current codebase)
than `category_rules.py`'s design (sub-project 2's spec — not yet merged,
possibly not yet even implemented, depending on which sub-project lands
first; see Rollout): a date rule must both find text **and** turn it into
a real `date`, so it needs a parser callback the same way `MoneyFormatRule`
needs a `normalizer`.

```python
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
    correctly by the same function — mirroring money_parser's
    generic_normalizer, which interprets every custom money pattern's
    named amount/mult/sign groups the same way. Returns None (never
    raises) for a numerically-plausible but calendar-invalid date, e.g.
    13/40/2026 — the pattern's \\d{1,2} groups admit strings date()
    itself rejects."""
    try:
        return date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
    except ValueError:
        return None


_NUMERIC_DATE_PATTERN = r"(?<!\d)(?P<month>\d{1,2})[/-](?P<day>\d{1,2})[/-](?P<year>\d{4})(?!\d)"


def default_rules() -> list[DateRule]:
    """Fresh instances every call — same mutation-isolation reasoning as
    money_parser.default_rules() and category_rules.default_rules()."""
    return [
        DateRule(
            id="numeric_date",
            label="Numeric date (MM/DD/YYYY, MM-DD-YYYY)",
            pattern=_NUMERIC_DATE_PATTERN,
            parser=_parse_named_groups,
            priority=0,
        ),
    ]


def build_custom_rule(pattern_str: str, label: Optional[str], index: int) -> DateRule:
    """Validates and builds a user-supplied date rule. A custom pattern
    must supply named groups (?P<year>...), (?P<month>...), (?P<day>...)
    — the same fixed-shape contract money_parser's custom rules use for
    (?P<amount>...): the pattern says WHERE the pieces are; the one shared
    _parse_named_groups says what to do with them. This is what makes a
    day-first document (Non-goals: not a built-in rule) usable without any
    code — a custom pattern with day before month in the text still names
    its groups year/month/day, and parses correctly. Raises ValueError
    with a user-facing message on invalid regex or a missing required
    group; never lets re.error escape to the GUI."""
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
            f"— missing: {', '.join(sorted(missing))}"
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


@dataclass(frozen=True)
class DateMatch:
    value: Optional[date]  # None if this regex match couldn't be parsed —
                            # kept, not dropped, so a calendar-invalid date
                            # sitting right next to an amount is visible as
                            # "something was here but unreadable" rather
                            # than invisible.
    raw_text: str  # the literal matched substring, e.g. "06/14/2026" —
                    # carried everywhere a suggestion is shown, the same
                    # way MoneyMatch.raw_text is, so a human (or an export
                    # reader) can check a suggestion against its source
                    # instead of trusting a bare parsed date.
    start: int  # character offset into the text that was searched


def find_dates(text: str, rules: list[DateRule]) -> list[DateMatch]:
    """Every date-*shaped* match in `text`, across all enabled rules —
    including ones that matched the pattern but failed to parse
    (`value=None`). Overlapping matches from different rules are resolved
    exactly like find_money_matches: sorted by position, then by match
    length (longest wins), then by rule priority (lowest wins); accepted
    greedily, left to right, never overlapping. With one built-in rule
    this never triggers; it exists so a custom rule that overlaps the
    built-in one (or another custom rule) resolves deterministically
    instead of however regex iteration order happens to visit them."""
    candidates = []
    for rule in rules:
        if not rule.enabled:
            continue
        for m in rule.compiled.finditer(text):
            candidates.append((m.start(), m.end(), rule.priority, m))

    candidates.sort(key=lambda c: (c[0], -(c[1] - c[0]), c[2]))
    accepted: list[DateMatch] = []
    cursor = 0
    rule_by_span: dict[tuple[int, int], DateRule] = {
        (m.start(), m.end()): rule
        for rule in rules
        if rule.enabled
        for m in rule.compiled.finditer(text)
    }
    for start, end, _, m in candidates:
        if start >= cursor:
            rule = rule_by_span[(start, end)]
            accepted.append(DateMatch(value=rule.parser(m), raw_text=m.group(0), start=start))
            cursor = end
    return accepted


def nearest_date(candidates: list[DateMatch], target_offset: int) -> Optional[DateMatch]:
    """The single date-shaped match closest to `target_offset`, by
    absolute character distance — or None if there are no candidates at
    all. Deliberately does NOT skip past an unparseable-but-closer
    candidate to reach a more distant, parseable one: if the nearest
    date-shaped text to an amount couldn't be read as a real date (e.g. a
    calendar-invalid OCR misread), this reports "no suggestion", not a
    confident, wrong substitute from somewhere else in the document — the
    caller checks `.value is None` and treats that the same as "nothing
    found". Ties (equidistant candidates) resolve to whichever appears
    EARLIER in the text — a stable, deterministic rule stated explicitly
    so two implementers test-first don't pick different tie-breaks."""
    if not candidates:
        return None
    return min(candidates, key=lambda c: (abs(c.start - target_offset), c.start))
```

**On the `find_dates`/`rule_by_span` shape above**: it's written to make the
priority-based conflict resolution concrete and testable (mirroring
`find_money_matches` closely enough that a reader of one recognizes the
other), not because a `dict` keyed by span is the only reasonable
implementation — an implementer may restructure this internally as long
as the same three guarantees hold and are tested: overlapping matches
never both survive, `priority` breaks ties among same-position candidates,
and the returned list is in text order.

### `DocumentResult`/`MatchRecord` changes (`cost_extractor/pipeline.py`)

```python
@dataclass
class DocumentResult:
    ...
    # All of this document's segments' text, concatenated at extraction
    # time with a "\n\n" separator between segments (so a date at the very
    # end of one page's text can never appear adjacent to text at the
    # start of the next). Segments are transient — gone once run_pipeline
    # returns — so this is captured now for on-demand date suggestion
    # later, in the GUI, the same reasoning MatchRecord.line_text already
    # uses for categorization, at document rather than line scope.
    full_text: str = ""
```

```python
@dataclass
class MatchRecord:
    ...
    # This match's own character offset within its DocumentResult's
    # full_text — not within its own segment. Needed to compute "nearest
    # date": comparing a match's position to every date candidate found
    # anywhere in the document only makes sense if both are measured in
    # the same coordinate space.
    doc_offset: int = 0
    # Every human decision about this amount's spend date, in order —
    # same append-only discipline, same Optional[T] reasoning as
    # category_revisions ("no date yet" is a real, expected state).
    spend_date_revisions: list[Revision[Optional[date]]] = field(default_factory=list)

    @property
    def spend_date_reviewed(self) -> bool:
        return bool(self.spend_date_revisions)

    @property
    def effective_spend_date(self) -> Optional[date]:
        return latest_value(self.spend_date_revisions, None)
```

Both are populated in `_process_single_file`'s match-building loop. This
requires reworking that loop slightly: it currently processes one segment
at a time with no notion of a running document-level offset. Add a
cumulative `doc_cursor` that starts at `0`, and after each segment's
matches are built, advances by `len(segment.text) + 2` (`+2` for the
`"\n\n"` separator that will join this segment's text into `full_text`).
Each match's `doc_offset` is `doc_cursor + m.start` (its local offset
within the segment, plus everything already accumulated before this
segment). `full_text` itself is assembled the same way, by joining every
segment's `.text` with `"\n\n"`, once per document, after the per-segment
loop completes — not per match.

### GUI changes (`cost_extractor/gui.py`)

**New `App.__init__` state**: `self.date_rules: list[DateRule] =
date_rules.default_rules()`, `self._date_suggestions: dict[int,
Optional[date]] = {}` (same `id(match)`-keyed cache shape as
`_second_opinions`/`_category_suggestions`), invalidated on rule changes
the identical way `_category_suggestions` is.

**Import as `from cost_extractor import date_rules`**, module-qualified —
same reasoning as `category_rules`: matches this file's established
convention, and `App.suggest_spend_date` (the method) would otherwise
collide with a same-named free function.

**`App.suggest_spend_date(match) -> Optional[date]`** — computed on
demand, cached per match:

```python
def suggest_spend_date(self, match: MatchRecord) -> Optional[date]:
    cached = self._date_suggestions.get(id(match), _UNREAD)
    if cached is not _UNREAD:
        return cached
    document = self._document_for(match)
    candidates = date_rules.find_dates(document.full_text, self.date_rules)
    nearest = date_rules.nearest_date(candidates, match.doc_offset)
    # nearest_date now returns the closest DateMatch (or None), not a bare
    # date — .value is None when the closest date-shaped text nearby
    # failed to parse (e.g. calendar-invalid), and that's still "no
    # suggestion," not license to fall back to a more distant candidate.
    suggestion = nearest.value if nearest is not None else None
    self._date_suggestions[id(match)] = suggestion
    return suggestion
```

`self._document_for(match)` is a new small helper (`App` currently has no
way to go from a `MatchRecord` back to its owning `DocumentResult` — every
existing flow iterates `for doc in ... for m in doc.matches` and never
needs the reverse direction). Built once per run, in `_run_worker`
(`gui.py:287-298`), right where `self.last_result` is already set:

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

    def _document_for(self, match: MatchRecord) -> DocumentResult:
        return self._match_documents[id(match)]
```

`self._match_documents: dict[int, DocumentResult] = {}` is initialized
empty in `App.__init__`, alongside the other new state above, so
`_document_for` has something defined to look up before the first run
completes (it's never called before then, but an uninitialized attribute
would be a real `AttributeError` risk for any test that constructs an
`App` and calls `suggest_spend_date` without first loading a result).

**`App.confirm_spend_date(match, date_str, note=None) -> Optional[str]`**
and **`App.accept_date_suggestion(match, note=None) -> Optional[str]`**
mirror `confirm_category`/`accept_category_suggestion` exactly, including
`accept_date_suggestion`'s required `None`-guard (a document can genuinely
contain no date-like text at all). `confirm_spend_date` parses `date_str`
by running `date_rules.find_dates(date_str, self.date_rules)` against the
typed text alone (not the document) and taking the first accepted match
— reject with an error message ("Couldn't recognize that as a date")
if `find_dates` returns nothing, same shape as `apply_correction`'s
`parse_amount` failure. If more than one rule's pattern matches the
typed string (e.g. it's short enough to satisfy two different custom
patterns), `find_dates`'s own overlap resolution already picked exactly
one non-overlapping match by the same position/length/priority ordering
used everywhere else — there is no second, separate ambiguity to resolve
here; a human typing a full date string (rather than pasting arbitrary
document text) is exactly the shape `find_dates` already handles
deterministically.

**`App.confirm_no_date(match, note=None) -> None`** — the reviewer's
explicit "this amount has no associated date" action, distinct from
`accept_date_suggestion`'s `None`-guard (which fires automatically when
there's nothing to accept). Where `accept_date_suggestion` refuses to act
when there's no suggestion, `confirm_no_date` is a deliberate human
decision available regardless of whether a suggestion exists — a
reviewer who disagrees with a found-but-wrong suggestion needs a way to
say "no, none of this document's dates apply here" rather than being
stuck between confirming a wrong date and leaving the match perpetually
unreviewed. It calls `record_revision(match.spend_date_revisions, None,
note=note or "confirmed no associated date")`, exactly the shape
`confirm_category`/`confirm_spend_date` already use, just with a fixed
`None` value instead of a parsed one. This is what makes
`spend_date_reviewed=True` with `effective_spend_date=None` a state the
UI and report both *produce* on purpose, not an edge case that merely
happens to be reachable — see `spend_date_label` and
`_spend_by_month_rows` below, both of which handle it explicitly rather
than assuming it can't occur.

**"Date Formats" panel** — new GUI panel listing `self.date_rules`,
mirroring the Categories panel's own list-with-checkboxes structure
(enable/disable per rule, an "Add custom pattern…" entry point). Three
new methods, each invalidating `self._date_suggestions` afterward for the
same reason `add_category_rule`/`remove_category_rule`/
`toggle_category_rule` invalidate `_category_suggestions`: a suggestion
cached under the old rule set is stale the instant the rule set changes,
and a stale cached `None` (interpreted as "unreadable" or "no rule
matched") is exactly as wrong as a stale cached wrong date — evicting the
whole `_date_suggestions` dict makes correctness independent of which
matches are affected by any specific rule change.

- **`App.add_date_rule(pattern_str, label=None) -> Optional[str]`** —
  calls `date_rules.build_custom_rule(pattern_str, label,
  index=len(self.date_rules))`, appends on success, returns `None`;
  catches `ValueError` from `build_custom_rule` and returns its message
  unappended, same error-surfacing shape as `add_category_rule`. Clears
  `self._date_suggestions` on success only — a rejected pattern changed
  nothing, so nothing needs invalidating.
- **`App.remove_date_rule(rule_id) -> None`** — removes by id, refuses
  (no-ops) if `rule_id` names a built-in rule, same "built-ins are
  disableable but not deletable" convention `remove_category_rule`
  established. Clears `self._date_suggestions`.
- **`App.toggle_date_rule(rule_id, enabled) -> None`** — flips
  `enabled` on the matching rule (built-in rules ARE toggleable, only
  removal is restricted). Clears `self._date_suggestions`.

**On a third confirm-workflow instance**: sub-project 2 deferred sharing
UI structure between the OCR review pane and the category window,
reasoning that a second instance was the point to *notice* the pattern,
and a third would be the point to actually extract it. This is that
third instance. Whether to now build a shared "suggest + confirm + queue"
component, or ship a third bespoke window, is a real decision — but it's
this sub-project's decision to make with full information (three working
examples in hand), not something to pre-decide in this spec before
seeing how the category window actually turned out in practice. Flag your
preference during spec review; absent one, default to a third bespoke
window (consistent with "duplicate now, generalize from real examples"),
sized at implementation time once the actual category-window code exists
to compare against.

### Report changes (`cost_extractor/report.py`)

**`build_workbook` gains a third optional parameter**, following the
exact pattern `category_rules` established: `date_rules:
Optional[list[DateRule]] = None`, `active_date_rules = date_rules or []`.
Same backward-compatibility reasoning: every existing call site (13 in
`main` today; 13 more sub-project 2 would add once its own report tests
land, if it has landed by the time this sub-project is implemented — see
Rollout) keeps compiling unchanged. Only `gui.py`'s `export_report` needs
an actual code change, to pass `self.date_rules` — alongside
`self.category_rules` if sub-project 2 has landed by then, or alone if
this sub-project is implemented first.

`report.py` does not currently import `Decimal` (verified against the
current file — its only imports are `Path`, `Optional`, `Workbook`,
`PipelineResult`, `format_revision_timestamp`); `_spend_by_month_rows`
below is the first thing in this file to need it, for its running
per-month totals, so add `from decimal import Decimal` alongside the
existing imports. If sub-project 2 lands first, its own
`_category_summary_rows` will have already added this same import —
check before adding a duplicate.

Flag for whoever writes the implementation plan: by this point
`build_workbook` has three optional keyword-shaped parameters
(`category_rules`, `date_rules`, and implicitly whatever sub-project 2
already added). If a fourth dimension is ever proposed, that is the
natural point to consolidate into a single config object rather than
keep growing the parameter list — not before, per the same "generalize
from real instances, not speculatively" reasoning used for the UI
question above.

**Details sheet** gains `Spend Date`/`Spend Date Review` columns,
identical shape to `Category`/`Category Review`. Unlike
`category_label(match, rules)`, this needs the owning `DocumentResult`
too (to reach `full_text`) — `build_workbook`'s existing Details loop
(`for doc in result.documents: for m in doc.matches`) already has `doc`
in scope at that point, so it's passed straight through rather than
re-derived:

```python
def spend_date_label(match, doc: "DocumentResult", rules: list["DateRule"]) -> str:
    if match.spend_date_reviewed:
        # A human can confirm "no date applies" (App.confirm_no_date) —
        # that's a completed review, not a missing one, so it gets its
        # own label rather than falling into effective_spend_date.isoformat()
        # (which would raise AttributeError on None) or being reported the
        # same as "nobody has looked yet."
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

called as `spend_date_label(m, doc, active_date_rules)` inside the
existing loop. Three distinct outcomes, three distinct labels: confirmed
date, confirmed no-date, and not-yet-reviewed (with or without a
suggestion) — matching the three-way split `_spend_by_month_rows` makes
below, so the Details sheet and the Spend By Month sheet never disagree
about which bucket a match falls into.

**`PipelineResult.unreviewed_date_count`** — a headline property
mirroring `uncategorized_count` (sub-project 2) and `unreviewed_ocr_count`
(sub-project 1): `sum(1 for doc in self.documents for m in doc.matches if
not m.spend_date_reviewed)`. Surfaced in the Summary sheet as a new row
("Dates Not Yet Reviewed", `_as_number(result.unreviewed_date_count)`),
appended after the existing rows in the same place `uncategorized_count`
is surfaced — this sub-project's equivalent of "how much of the total
still needs a human," visible without opening the Details sheet, the
same reasoning sub-project 1 gave for surfacing `unreviewed_ocr_count`
at the top level rather than leaving it something you'd only discover
row by row.

`_DETAILS_HEADER` gains both, appended at the end (same convention as
`Category`/`Category Review`).

**Revisions sheet**: sub-project 2's spec already anticipated this
exact addition — a `"Spend Date"` value for the existing `Dimension`
column, via a new `_spend_date_revision_rows(match)` mirroring
`_category_revision_rows` exactly (`"Undated"` in place of
`"Uncategorized"` for `None`, `revision.value.isoformat()` in place of a
bare string for a real date). `build_workbook`'s Revisions-sheet loop
calls `_revision_rows`, then `_category_revision_rows` (if sub-project 2
has landed), then `_spend_date_revision_rows`, per match — value history
before category history before date history, when more than one exists.

**New "Spend By Month" sheet** — the one place in this whole audit-trail
design that is genuinely, deliberately chronological, unlike Revisions
(document order) or Categories (alphabetical). One row per calendar
month present in the data, sorted chronologically, plus two final rows
— every match lands in exactly one bucket, so the sheet's rows sum to
`result.effective_grand_total` with no match silently missing from all
of them:

```python
_SPEND_BY_MONTH_HEADER = ["Month", "Amount", "Match Count"]


def _spend_by_month_rows(result: PipelineResult) -> list[list]:
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

Only *confirmed* dates feed the monthly rows — the same "every match
needs a confirmed decision before it counts toward a specific bucket"
discipline sub-project 2 established for category totals, not sub-project
1's OCR-value pattern (where an unconfirmed reading still counts, just
gets flagged). An unconfirmed date suggestion would put a dollar amount
in a specific month based on a machine guess nobody checked; the whole
point of "produced in discovery" is that a reader can trust which month
a number is claimed to belong to. But an unconfirmed match's amount
still isn't dropped from the sheet entirely — it lands in "Not Yet
Reviewed," distinct from "No Date (confirmed)," because those are two
different facts: one is a human decision (`confirm_no_date` was called),
the other is the absence of any decision yet. Collapsing them into one
`Undated` row (the original design) made a reviewed match indistinguishable
from an unreviewed one — exactly the ambiguity `spend_date_label` above
was also fixed to avoid.

**Sheet creation order** in `build_workbook`: Summary, Details,
Categories (if sub-project 2 has landed), Revisions, Spend By Month —
Spend By Month last, since it's the sub-project's actual deliverable and
reads naturally as the final, synthesized view after the more granular
sheets above it.

## Data flow

Extraction captures `full_text` (once per document) and `doc_offset`
(once per match) — nothing else about extraction changes. The GUI
computes suggestions on demand from `full_text` + live `date_rules`,
confirms into `spend_date_revisions` via `record_revision`. `report.py`
independently recomputes suggestions at export time from whatever
`date_rules` list it's given, exactly paralleling how it already handles
category suggestions — no shared cache between GUI and report.

## Error handling

An unparseable date string is rejected by `confirm_spend_date`, same
message shape as `apply_correction`'s rejection. A numerically-plausible
but calendar-invalid date (e.g. `13/40/2026`) is kept as a `DateMatch`
with `value=None` by `find_dates` (its `raw_text` still visible, not
dropped) rather than silently excluded — `nearest_date` then reports "no
suggestion" if this is the closest candidate, rather than reaching past
it to a more distant, valid one (see `date_rules.py`'s `nearest_date`
docstring above). `_parse_named_groups` itself still swallows the
`ValueError` internally and returns `None` rather than raising, the same
way an existing normalizer's `ValueError` already gets caught rather than
propagated — the difference from the original design is only that the
*match itself* is no longer thrown away along with the failed parse. An
invalid `build_custom_rule` pattern (bad regex, or missing a required
named group) raises `ValueError` with a user-facing message, caught by
`App.add_date_rule` and surfaced as a rejection, never left to crash the
GUI or silently fail to add the rule.

## Testing

- New `tests/test_date_rules.py`: `_parse_named_groups` accepts a valid
  date and returns `None` for a calendar-invalid one (both digit-count-
  plausible, via the built-in numeric pattern's match groups); `find_dates`
  returns a `DateMatch` for every rule match including calendar-invalid
  ones (`value=None`, `raw_text` still populated — not dropped);
  `find_dates` resolves an overlap between two rules by position, then
  length, then priority, exactly mirroring a `find_money_matches` overlap
  test (construct two rules whose patterns overlap on the same text and
  assert which one's match survives, not just that only one does);
  `nearest_date` picks the closer of two candidates, resolves a tie
  deterministically toward the earlier one (construct two candidates
  genuinely equidistant from the target and assert which wins, not just
  that one is returned), and returns the closest candidate even when its
  `value is None` rather than skipping to a more distant valid one
  (the direct regression test for the substitution bug this design
  fixes); `build_custom_rule` accepts a pattern with all three named
  groups in a non-standard (day-first) order and parses it correctly,
  rejects a pattern missing a required group, rejects invalid regex,
  rejects a pattern `_is_pattern_too_slow` flags.
- `doc_offset`/`full_text` capture: a document with 3 segments produces
  `full_text` joined with `"\n\n"`; a match in the 2nd segment has
  `doc_offset` equal to the first segment's length plus the separator
  plus its own local offset — asserted with concrete numbers, not just
  "some offset was set." Build this as a test of the real
  `_process_single_file` cursor-advancing loop, not a hand-built
  `DocumentResult`/`MatchRecord` pair with `doc_offset` set by the test
  itself — a hand-built fixture would let a bug in the cursor arithmetic
  pass unnoticed. Construct 3 `TextSegment`s directly with known text
  (`TextSegment(text=..., location=..., provenance=...)`, the same
  constructor `tests/test_span_evidence.py`'s `_segment` helper already
  uses), wrap them in an `ExtractionResult`, and monkeypatch
  `cost_extractor.pipeline._extract` to return it for one fake
  `DiscoveredFile` — then call `_process_single_file` (or `run_pipeline`
  with that one file) for real and assert the resulting matches'
  `doc_offset` values and the `DocumentResult.full_text` against the
  concrete numbers computed by hand from the 3 segments' known lengths.
- New GUI tests (mirroring sub-project 2's category tests structurally):
  cache invalidation with the same explicit seed-before-mutate ordering,
  including that `add_date_rule`/`remove_date_rule`/`toggle_date_rule`
  each independently clear `_date_suggestions`; `confirm_spend_date`/
  `accept_date_suggestion` append correctly, including a second-
  confirmation scenario; `accept_date_suggestion`'s `None`-guard when no
  date exists anywhere in the document; `confirm_spend_date` rejects
  unparseable text; `confirm_no_date` records a `None`-valued revision
  and flips `spend_date_reviewed` to `True` while leaving
  `effective_spend_date` `None`; `add_date_rule` surfaces
  `build_custom_rule`'s rejection message unchanged and leaves
  `self.date_rules` untouched on failure.
- New report tests: Details' `Spend Date`/`Spend Date Review` columns
  covering all three outcomes — confirmed date, confirmed no-date
  (`"No Date (confirmed)"`), suggested-unconfirmed, and `"Undated"` (no
  suggestion at all); Spend By Month's monthly rows sum correctly across
  two documents with different months, sorted chronologically (construct
  months out of insertion order to prove sorting, not incidental dict
  order); `"No Date (confirmed)"` and `"Not Yet Reviewed"` each appear
  only when at least one match falls into that bucket and are omitted
  otherwise (mirroring the Categories sheet's conditional-row precedent);
  a match confirmed via `confirm_no_date` appears in `"No Date
  (confirmed)"`, not `"Not Yet Reviewed"`, and a merely-unreviewed match
  the reverse (the two-bucket distinction, directly tested, not just
  asserted in prose); a match whose *suggested* date would put it in one
  month but has no *confirmed* date does not appear in that month's total
  (the confirmed-only discipline for monthly rows); the three Spend By
  Month buckets' amounts sum to `result.effective_grand_total`; Revisions
  sheet gets a `"Spend Date"` dimension row alongside `"Value"` (and
  `"Category"`, if that sub-project's own tests are already in place).
- Ripple check (mirroring sub-project 2's precedent): grep `build_workbook(`
  at implementation time and confirm every pre-existing call site still
  compiles unchanged with the new parameter defaulted away.

## Rollout

Lands on `spend-over-time`, branched from `main` (not from sub-project
2's `spend-categorization` branch — this sub-project doesn't depend on
category machinery, only on sub-project 1's `Revision[T]` mechanism,
already merged).

**Ordering with sub-project 2 is genuinely open** — whichever of the two
sub-projects is implemented and merged to `main` first is the one that
actually introduces the Revisions sheet's `Dimension` column; the second
one just adds its own value to a column that already exists. If this
sub-project lands first, its own Revisions-sheet work introduces
`Dimension` with only `"Value"`/`"Spend Date"` as possible values, and
sub-project 2's later implementation plan adjusts to add `"Category"` to
an existing column rather than creating it — the mirror image of what
this spec currently describes. Whoever writes the second sub-project's
implementation plan should re-check the actual merged state of
`report.py` at that time rather than trust either spec's assumed
ordering.
