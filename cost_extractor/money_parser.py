"""Regex-based rule engine for detecting USD dollar amounts in text."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

CURRENCY = r"(?:US\$|USD|\$)"
_INT_OR_DECIMAL = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"

STANDARD_PATTERN = (
    rf"(?<![\w.]){CURRENCY}\s?(?P<amount>{_INT_OR_DECIMAL})(?![\w.])"
    rf"|(?<![\w.])(?P<amount2>{_INT_OR_DECIMAL})\s?{CURRENCY}(?![\w.])"
)


@dataclass
class MoneyMatch:
    rule_id: str
    raw_text: str
    start: int
    end: int
    value: Decimal


@dataclass
class MoneyFormatRule:
    id: str
    label: str
    pattern: str
    normalizer: Callable[[re.Match], Optional[Decimal]]
    priority: int = 50
    enabled: bool = True
    built_in: bool = True
    flags: int = re.IGNORECASE
    compiled: re.Pattern = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.compiled = re.compile(self.pattern, self.flags)


MULTIPLIER_MAP = {
    "k": Decimal(1_000),
    "thousand": Decimal(1_000),
    "m": Decimal(1_000_000),
    "million": Decimal(1_000_000),
    "b": Decimal(1_000_000_000),
    "billion": Decimal(1_000_000_000),
}

MULTIPLIER_TOKEN = r"[kK]|[mM]|[bB]|[Tt]housand|[Mm]illion|[Bb]illion"

SHORTHAND_PATTERN = (
    rf"(?<![\w.]){CURRENCY}\s?(?P<amount>\d+(?:\.\d+)?)\s?(?P<mult>{MULTIPLIER_TOKEN})\b"
)


def _parse_number_token(token: str) -> Decimal:
    return Decimal(token.replace(",", ""))


def _apply_multiplier(value: Decimal, mult_token: Optional[str]) -> Decimal:
    if not mult_token:
        return value
    return value * MULTIPLIER_MAP[mult_token.lower()]


def _standard_normalizer(match: re.Match) -> Optional[Decimal]:
    groups = match.groupdict()
    amount = groups.get("amount") or groups.get("amount2")
    if amount is None:
        return None
    try:
        return _parse_number_token(amount)
    except InvalidOperation:
        return None


def _shorthand_normalizer(match: re.Match) -> Optional[Decimal]:
    groups = match.groupdict()
    amount = groups.get("amount")
    if amount is None:
        return None
    try:
        value = _parse_number_token(amount)
    except InvalidOperation:
        return None
    return _apply_multiplier(value, groups.get("mult"))


PAREN_NEG_PATTERN = (
    rf"\(\s?{CURRENCY}\s?(?P<amount>\d{{1,3}}(?:,\d{{3}})*(?:\.\d{{1,2}})?|\d+(?:\.\d{{1,2}})?)"
    rf"\s?(?P<mult>{MULTIPLIER_TOKEN})?\s?\)"
)


def _paren_negative_normalizer(match: re.Match) -> Optional[Decimal]:
    groups = match.groupdict()
    amount = groups.get("amount")
    if amount is None:
        return None
    try:
        value = _parse_number_token(amount)
    except InvalidOperation:
        return None
    value = _apply_multiplier(value, groups.get("mult"))
    return -value


def default_rules() -> list[MoneyFormatRule]:
    """Returns a fresh list of built-in rule instances on every call.

    MoneyFormatRule.enabled is mutated in place by the GUI, so callers
    must never share a cached/module-level list of rule instances.
    """
    return [
        MoneyFormatRule(
            id="paren_negative",
            label="Accounting negatives in parentheses (($1,200.00))",
            pattern=PAREN_NEG_PATTERN,
            normalizer=_paren_negative_normalizer,
            priority=0,
        ),
        MoneyFormatRule(
            id="shorthand_kmb",
            label="Shorthand K/M/B ($1.5M, $250K, 2.3 million)",
            pattern=SHORTHAND_PATTERN,
            normalizer=_shorthand_normalizer,
            priority=1,
        ),
        MoneyFormatRule(
            id="standard",
            label="Standard amounts ($1,234.56, USD 1,234.56)",
            pattern=STANDARD_PATTERN,
            normalizer=_standard_normalizer,
            priority=2,
        ),
    ]


def find_money_matches(text: str, rules: list[MoneyFormatRule]) -> list[MoneyMatch]:
    candidates: list[MoneyMatch] = []
    for rule in rules:
        if not rule.enabled:
            continue
        for m in rule.compiled.finditer(text):
            value = rule.normalizer(m)
            if value is None:
                continue
            candidates.append(
                MoneyMatch(
                    rule_id=rule.id,
                    raw_text=m.group(0),
                    start=m.start(),
                    end=m.end(),
                    value=value,
                )
            )

    priority_by_rule = {rule.id: rule.priority for rule in rules}
    candidates.sort(
        key=lambda c: (c.start, -(c.end - c.start), priority_by_rule[c.rule_id])
    )

    accepted: list[MoneyMatch] = []
    cursor = 0
    for candidate in candidates:
        if candidate.start >= cursor:
            accepted.append(candidate)
            cursor = candidate.end

    return accepted


def generic_normalizer(match: re.Match) -> Optional[Decimal]:
    """Shared normalizer for all custom (user-supplied) rules.

    Requires a named `amount` group. Applies an optional `mult` group
    via MULTIPLIER_MAP, and negates when an optional `sign` group is
    present or the full match is parenthesized (mirrors paren_negative).
    """
    groups = match.groupdict()
    amount = groups.get("amount")
    if amount is None:
        return None
    try:
        value = _parse_number_token(amount)
    except InvalidOperation:
        return None
    value = _apply_multiplier(value, groups.get("mult"))
    if groups.get("sign") or "(" in match.group(0):
        value = -value
    return value


_REDOS_PROBE_STRINGS = ["a" * 22, "1" * 22, "$" + "1" * 22 + " " * 22]
_REDOS_PROBE_TIMEOUT_SECONDS = 0.1


def _is_pattern_too_slow(compiled: re.Pattern) -> bool:
    """Best-effort ReDoS canary: times synchronous matches against small
    adversarial probe strings and rejects if they're too slow.

    This is deliberately NOT implemented as a background thread with a
    timeout: Python's `re` engine holds the GIL for the entire duration
    of a single match call (no bytecode runs during it), so a waiting
    thread cannot even reacquire the GIL to notice its timeout elapsed
    until the runaway match finishes on its own — a preemptive timeout
    around a `re` call does not actually bound wall-clock time on
    CPython. Probe strings are kept short (len 20) so that even
    classic O(2^n) catastrophic-backtracking patterns finish within a
    few hundred milliseconds here, while legitimate patterns finish in
    microseconds. This will not catch a pattern that only blows up on
    much longer real-world input; it catches the common case where a
    pattern is already exponential at this small scale.
    """
    start = time.perf_counter()
    for s in _REDOS_PROBE_STRINGS:
        compiled.search(s)
        if time.perf_counter() - start > _REDOS_PROBE_TIMEOUT_SECONDS:
            return True
    return False


def build_custom_rule(
    pattern_str: str, label: Optional[str], index: int
) -> MoneyFormatRule:
    """Validates and builds a user-supplied custom MoneyFormatRule.

    Raises ValueError with a user-facing message on any validation
    failure; never lets re.error or a hang escape to the GUI.
    """
    try:
        compiled = re.compile(pattern_str, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"Invalid regex: {e}") from e

    if "amount" not in compiled.groupindex:
        raise ValueError("Pattern must include a named group (?P<amount>...)")

    if _is_pattern_too_slow(compiled):
        raise ValueError(
            "Pattern is too slow / potentially catastrophic backtracking; simplify it."
        )

    return MoneyFormatRule(
        id=f"custom_{index}",
        label=label or f"Custom pattern {index}",
        pattern=pattern_str,
        normalizer=generic_normalizer,
        priority=100 + index,
        enabled=True,
        built_in=False,
    )
