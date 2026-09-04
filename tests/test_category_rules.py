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
