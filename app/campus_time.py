"""The campus clock: one instant, formatted into the one line the model is shown.

WHY THIS IS NOT `datetime.now()`. Lambda's clock is UTC and nothing in the deployment
changes that, so a naive stamp tells the model it is 3am while the student typing the
message is awake at 8pm in San Jose. Every question a student asks about time is asked in
campus time, so the zone is fixed here rather than read from the environment: the process
timezone is an accident of where the code runs, and the answer must not be.

THE LINE SAYS ITS OWN ZONE. It carries the weekday, the date, the time, the abbreviation
the student would say out loud (PDT or PST, whichever is in force that day) and the IANA
name behind it. A bare "2:53 PM" is a number the model has to guess the frame of; the
frame is the part that stops it from converting.

THIS IS A CONTEXT PROJECTION AND ONLY THAT. The line is built for the model's copy of the
turn (orchestrator._build_user_message) and never for the student's stored message, which
stays the words the student typed (docs/accounts-and-storage.md, Turn lifecycle: the
context read and the display read are two projections of one stored row, and only the
first one gets this).

A ZONE THAT WILL NOT RESOLVE COSTS THE LINE, NEVER THE TURN. zoneinfo reads the host's tz
database, so a runtime shipped without one raises rather than returning a wrong answer.
The fallback is silence: no line at all, logged at WARNING. Falling back to UTC would be
the exact bug this module exists to prevent, and it would be invisible, since a UTC stamp
looks like a working feature right up until a student asks whether an office is open.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

# San Jose State's timezone, and the only one this app ever states a time in. An IANA name
# rather than a fixed offset, so the switch between PDT and PST is the tz database's job
# and not a thing anyone has to remember twice a year.
CAMPUS_TIMEZONE = "America/Los_Angeles"

# Weekday first, because "is the office open" is a weekday question before it is a clock
# question. `%-d` and `%-I` drop the leading zero (glibc and BSD both), so the line reads
# the way a person writes a time rather than "08:05 AM".
_STAMP_FORMAT = "%A, %B %-d, %Y at %-I:%M %p %Z"


def _campus_zone() -> ZoneInfo | None:
    try:
        return ZoneInfo(CAMPUS_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        logger.warning(
            "No tz database entry for %s, so this turn goes to the model with no time in "
            "it. A UTC stamp would be worse than none: it reads as a working clock and is "
            "wrong by seven or eight hours.",
            CAMPUS_TIMEZONE,
            exc_info=True,
        )
        return None


def campus_now() -> datetime | None:
    """The current instant, in campus time. None when the zone will not resolve."""
    zone = _campus_zone()
    if zone is None:
        return None
    return datetime.now(zone)


def campus_context_line(moment: datetime | None = None) -> str:
    """The one line of time the model is given, or "" when there is no clock to state.

    `moment` is an AWARE datetime in any zone, converted here, which is what makes the
    conversion testable against the failure it exists for: hand it the UTC instant a
    Lambda would see and the line comes back in campus time. Passing None reads the clock.
    """
    if moment is None:
        moment = campus_now()
        if moment is None:
            return ""
    else:
        zone = _campus_zone()
        if zone is None:
            return ""
        moment = moment.astimezone(zone)

    return (
        f"Current date and time on campus: {moment.strftime(_STAMP_FORMAT)} "
        f"({CAMPUS_TIMEZONE})."
    )
