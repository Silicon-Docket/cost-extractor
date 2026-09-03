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
