"""The campus clock. The zone is the assertion, because the zone is the bug.

Lambda's clock is UTC, so the failure this module exists to stop is not a missing time, it
is a confident wrong one: a student typing at 8pm on a Tuesday, told by their own advisor
that it is 3am on Wednesday. Every test here drives that instant.
"""

from datetime import datetime, timedelta, timezone

import campus_time


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_the_campus_zone_is_pinned_and_is_not_utc():
    """The one value this whole module turns on. It is a literal here for the same reason
    app/safety.py's contacts are literals: it is a fact about SJSU, not a knob."""
    assert campus_time.CAMPUS_TIMEZONE == "America/Los_Angeles"


def test_a_utc_instant_is_stated_in_campus_time_not_utc():
    """THE BUG, driven end to end. 03:14 UTC on Wednesday is 8:14pm on TUESDAY in San Jose:
    the hour is wrong, the day is wrong, and the weekday is wrong, which is three chances
    for an answer about office hours to be confidently useless."""
    line = campus_time.campus_context_line(_utc(2026, 8, 12, 3, 14))

    assert "Tuesday, August 11, 2026" in line
    assert "8:14 PM" in line
    assert "3:14 AM" not in line and "Wednesday" not in line


def test_the_line_names_its_zone_both_ways():
    """A time with no frame is a number the model has to guess at. It gets the abbreviation
    a student would say out loud and the IANA name behind it."""
    line = campus_time.campus_context_line(_utc(2026, 8, 12, 21, 53))

    assert "PDT" in line
    assert "(America/Los_Angeles)" in line


def test_the_line_carries_the_weekday():
    """"Is it open" is a weekday question before it is a clock question."""
    line = campus_time.campus_context_line(_utc(2026, 8, 15, 19, 0))

    assert line.startswith("Current date and time on campus: Saturday, August 15, 2026")


def test_the_abbreviation_follows_daylight_saving_rather_than_a_fixed_offset():
    """An IANA name rather than a hardcoded -7, so the November switch is the tz database's
    problem and not a thing anyone has to remember twice a year."""
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
    """The one way this can fail, and the fallback is SILENCE. A UTC stamp would look like
    a working clock and be wrong by eight hours, which is worse than a model that simply
    does not know what time it is. Logged at WARNING, because nothing else would show it."""

    def _no_tz_database(name):
        raise campus_time.ZoneInfoNotFoundError(name)

    monkeypatch.setattr(campus_time, "ZoneInfo", _no_tz_database)

    with caplog.at_level("WARNING"):
        assert campus_time.campus_context_line(_utc(2026, 8, 12, 3, 14)) == ""
        assert campus_time.campus_context_line() == ""
        assert campus_time.campus_now() is None

    assert "America/Los_Angeles" in caplog.text
