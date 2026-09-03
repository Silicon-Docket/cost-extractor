"""The append-only history behind every human correction.

A `MatchRecord`'s money value used to carry a single `corrected_value`
with no timestamp, overwritten by a second correction. This module is
what replaces that: every change is appended, never overwritten, so
correcting an amount twice (e.g. fixing your own typo) keeps both
corrections and both timestamps, not just the latest.

Generic so a future sub-project (spend categorization, spend date) can
reuse this exact mechanism instead of inventing its own audit shape.
"""

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
