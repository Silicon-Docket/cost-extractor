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


def test_find_dates_resolves_overlap_by_priority():
    # Two rules that both match the same span but parse it differently
    # (month-first vs day-first) -- proves priority actually selects
    # which candidate's PARSE wins, not just that dedup happened. Two
    # rules sharing a parser and producing the same value (as with a
    # custom rule overlapping the default month-first built-in) can't
    # distinguish a real priority tie-break from any other dedup rule.
    month_first = build_custom_rule(
        r"(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})", "Month-first", 0
    )
    day_first = build_custom_rule(
        r"(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4})", "Day-first", 1
    )
    month_first.priority = -1  # deliberately beats day_first's priority=101

    matches = find_dates("13/06/2026", [month_first, day_first])

    # month_first wins: month=13 is calendar-invalid -> value=None.
    # If day_first had won instead, this would be a valid date
    # (day=13, month=06 -> June 13, 2026).
    assert len(matches) == 1
    assert matches[0].value is None


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
