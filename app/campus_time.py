"""The campus clock: one instant, formatted into the one line the model is shown.

Lambda runs on UTC and an unresolvable zone costs the line rather than the turn; see
docs/chat-service.md, The campus clock.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

CAMPUS_TIMEZONE = "America/Los_Angeles"

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
    """Return the one line of time the model is given, or "" when there is no clock."""
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
