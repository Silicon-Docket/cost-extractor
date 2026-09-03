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
