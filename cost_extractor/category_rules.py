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
