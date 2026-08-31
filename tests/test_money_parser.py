from decimal import Decimal

import pytest

from cost_extractor.money_parser import build_custom_rule, default_rules, find_money_matches


def test_standard_rule_matches_dollar_prefixed_amount():
    rules = default_rules()

    matches = find_money_matches("Invoice total: $1,234.56 due", rules)

    assert len(matches) == 1
    assert matches[0].value == Decimal("1234.56")
    assert matches[0].rule_id == "standard"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$1234", Decimal("1234")),
        ("$1,000", Decimal("1000")),
        ("US$1,000.00", Decimal("1000.00")),
        ("USD 1,234.56", Decimal("1234.56")),
        ("1,234.56 USD", Decimal("1234.56")),
        ("total:$500", Decimal("500")),
    ],
)
def test_standard_rule_matches_various_dollar_formats(text, expected):
    rules = default_rules()

    matches = find_money_matches(text, rules)

    assert len(matches) == 1
    assert matches[0].value == expected


def test_standard_rule_does_not_truncate_shorthand_amount_when_alone():
    """$1.5M must match nothing under `standard` alone, not a truncated $1.5 —
    the trailing word character blocks the match entirely via lookahead."""
    rules = [r for r in default_rules() if r.id == "standard"]

    matches = find_money_matches("Budget: $1.5M", rules)

    assert matches == []


@pytest.mark.parametrize(
    "text",
    [
        "Call 555-1234",
        "invoice #4521",
        "dated 12/25/2024",
        "page 42",
        "chapter 7",
        "just a $ sign",
        "the year 2024",
        "3 million people",
        "v1.5",
    ],
)
def test_standard_rule_does_not_match_non_currency_text(text):
    rules = default_rules()

    matches = find_money_matches(text, rules)

    assert matches == []


def test_standard_rule_alone_ignores_enclosing_parens_and_stays_positive():
    """Documents behavior when only `standard` is enabled: parens around an
    amount are not treated as negation unless `paren_negative` is also on."""
    rules = [r for r in default_rules() if r.id == "standard"]

    matches = find_money_matches("Adjustment: ($500)", rules)

    assert len(matches) == 1
    assert matches[0].value == Decimal("500")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$1.5M", Decimal("1500000")),
        ("$250K", Decimal("250000")),
        ("$2.3B", Decimal("2300000000")),
        ("$2.3 million", Decimal("2300000")),
        ("$250 thousand", Decimal("250000")),
        ("$1 billion", Decimal("1000000000")),
    ],
)
def test_shorthand_kmb_rule_matches_and_expands(text, expected):
    rules = default_rules()

    matches = find_money_matches(text, rules)

    assert len(matches) == 1
    assert matches[0].rule_id == "shorthand_kmb"
    assert matches[0].value == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("($1,200.00)", Decimal("-1200.00")),
        ("($500)", Decimal("-500")),
        ("($1.5M)", Decimal("-1500000")),
        ("($250K)", Decimal("-250000")),
    ],
)
def test_paren_negative_rule_matches_and_negates(text, expected):
    rules = default_rules()

    matches = find_money_matches(text, rules)

    assert len(matches) == 1
    assert matches[0].rule_id == "paren_negative"
    assert matches[0].value == expected


def test_paren_negative_wins_overlap_against_standard_and_shorthand():
    rules = default_rules()

    matches = find_money_matches("Adjustment: ($1,200.00) this quarter", rules)

    assert len(matches) == 1
    assert matches[0].rule_id == "paren_negative"


def test_disabled_rule_produces_no_matches():
    rules = default_rules()
    for rule in rules:
        if rule.id == "standard":
            rule.enabled = False

    matches = find_money_matches("Total: $1,234.56", rules)

    assert matches == []


def test_default_rules_returns_fresh_instances_each_call():
    first_call = default_rules()
    for rule in first_call:
        rule.enabled = False

    second_call = default_rules()

    assert all(rule.enabled for rule in second_call)


def test_two_distinct_amounts_are_both_captured_without_merging():
    rules = default_rules()

    matches = find_money_matches("Paid $100 now and $2.5M later", rules)

    values = sorted(m.value for m in matches)
    assert values == [Decimal("100"), Decimal("2500000")]


def test_shorthand_kmb_requires_currency_marker():
    """`3 million people` must not match — no $ sign, so this is out of
    scope by design; a user wanting this can add it as a custom rule."""
    rules = default_rules()

    matches = find_money_matches("3 million people attended", rules)

    assert matches == []


def test_build_custom_rule_matches_and_normalizes():
    rule = build_custom_rule(r"(?P<amount>\d+(?:\.\d{2})?)\s?EUR", "Euro amount", 0)

    matches = find_money_matches("Cost: 45.00 EUR total", [rule])

    assert len(matches) == 1
    assert matches[0].value == Decimal("45.00")
    assert matches[0].rule_id == "custom_0"
    assert rule.enabled is True
    assert rule.built_in is False


def test_build_custom_rule_rejects_invalid_regex_syntax():
    with pytest.raises(ValueError, match="Invalid regex"):
        build_custom_rule(r"(?P<amount>\d+", "Broken", 0)


def test_build_custom_rule_requires_named_amount_group():
    with pytest.raises(ValueError, match="amount"):
        build_custom_rule(r"\d+\s?EUR", "No group", 0)


def test_build_custom_rule_rejects_catastrophic_backtracking_pattern():
    with pytest.raises(ValueError, match="slow|backtrack"):
        build_custom_rule(r"(?P<amount>(a+)+)b", "Evil", 0)


def test_build_custom_rule_supports_mult_and_parenthesis_negation():
    rule = build_custom_rule(
        r"\((?P<amount>\d+(?:\.\d+)?)\s?(?P<mult>K|M)?\s?EUR\)", "Euro paren", 1
    )

    matches = find_money_matches("Loss: (1.5MEUR) this year", [rule])

    assert len(matches) == 1
    assert matches[0].value == Decimal("-1500000")
