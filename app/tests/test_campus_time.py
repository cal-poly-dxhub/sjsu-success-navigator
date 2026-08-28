"""The campus clock. The zone is the assertion, because the zone is the bug."""

from datetime import datetime, timedelta, timezone

import campus_time


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_the_campus_zone_is_pinned_and_is_not_utc():
    """The one value this whole module turns on, and it is a literal here deliberately."""
    assert campus_time.CAMPUS_TIMEZONE == "America/Los_Angeles"


def test_a_utc_instant_is_stated_in_campus_time_not_utc():
    """The bug, driven end to end: 03:14 UTC on Wednesday is 8:14pm on Tuesday in San Jose."""
    line = campus_time.campus_context_line(_utc(2026, 8, 12, 3, 14))

    assert "Tuesday, August 11, 2026" in line
    assert "8:14 PM" in line
    assert "3:14 AM" not in line and "Wednesday" not in line


def test_the_line_names_its_zone_both_ways():
    """A time with no frame is a number the model has to guess at."""
    line = campus_time.campus_context_line(_utc(2026, 8, 12, 21, 53))

    assert "PDT" in line
    assert "(America/Los_Angeles)" in line


def test_the_line_carries_the_weekday():
    """"Is it open" is a weekday question before it is a clock question."""
    line = campus_time.campus_context_line(_utc(2026, 8, 15, 19, 0))

    assert line.startswith("Current date and time on campus: Saturday, August 15, 2026")


def test_the_abbreviation_follows_daylight_saving_rather_than_a_fixed_offset():
    """An IANA name rather than a hardcoded offset, so the switch is the tz database's job."""
    summer = campus_time.campus_context_line(_utc(2026, 7, 1, 19, 0))
    winter = campus_time.campus_context_line(_utc(2026, 12, 1, 19, 0))

    assert "PDT" in summer
    assert "PST" in winter


def test_the_hour_carries_no_leading_zero():
    """The line reads the way a person writes a time. 8:05 AM, never 08:05 AM."""
    assert "at 8:05 AM" in campus_time.campus_context_line(_utc(2026, 8, 12, 15, 5))


def test_reading_the_clock_returns_an_aware_campus_datetime():
    now = campus_time.campus_now()

    assert now is not None
    assert now.tzinfo is not None
    assert str(now.tzinfo) == campus_time.CAMPUS_TIMEZONE
    # Sanity, not precision: the wall clock in San Jose is hours off UTC, never at it.
    assert now.utcoffset() in (timedelta(hours=-7), timedelta(hours=-8))


def test_a_runtime_with_no_tz_database_costs_the_line_and_never_falls_back_to_utc(
    monkeypatch, caplog
):
    """The fallback is silence: a UTC stamp reads as a working clock and is hours wrong."""

    def _no_tz_database(name):
        raise campus_time.ZoneInfoNotFoundError(name)

    monkeypatch.setattr(campus_time, "ZoneInfo", _no_tz_database)

    with caplog.at_level("WARNING"):
        assert campus_time.campus_context_line(_utc(2026, 8, 12, 3, 14)) == ""
        assert campus_time.campus_context_line() == ""
        assert campus_time.campus_now() is None

    assert "America/Los_Angeles" in caplog.text
