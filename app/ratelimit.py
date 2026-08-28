"""The per-user daily message cap, the one control that bounds what one account spends.

It counts attempts rather than answers, on a fixed UTC day, and it fails open.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitWindow:
    key: str
    reset_at: datetime
    expires_at: int

    def retry_after_seconds(self, now: datetime) -> int:
        remaining = (self.reset_at - now).total_seconds()
        if remaining <= 1:
            return 1
        return int(remaining) + (1 if remaining % 1 else 0)


def window_for(now: datetime) -> RateLimitWindow:
    now = now.astimezone(timezone.utc)
    reset_at = datetime.combine(
        now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    )
    return RateLimitWindow(
        key=now.strftime("%Y-%m-%d"),
        reset_at=reset_at,
        expires_at=int(reset_at.timestamp()),
    )


@dataclass(frozen=True)
class RateLimitRefusal:
    limit: int
    reset_at: datetime
    retry_after_seconds: int

    @property
    def reset_at_iso(self) -> str:
        return self.reset_at.isoformat(timespec="seconds").replace("+00:00", "Z")

    @property
    def message(self) -> str:
        return (
            f"You have reached your daily limit of {self.limit} messages. "
            "Your limit resets at midnight UTC."
        )


def claim_turn(*, store, user_id, client_id, settings, now=None):
    """Take this turn out of the user's daily allowance. None to continue, or a refusal."""
    limit = settings.daily_message_limit
    if limit <= 0:
        return None

    if client_id is not None and client_id in settings.rate_limit_exempt_client_ids:
        return None

    window = window_for(now or datetime.now(timezone.utc))

    try:
        allowed = store.claim_message_allowance(
            user_id=user_id,
            window_key=window.key,
            limit=limit,
            expires_at=window.expires_at,
        )
    except Exception:
        logger.exception(
            "Could not check the daily message limit; allowing this turn unmetered"
        )
        return None

    if allowed:
        return None

    logger.info("Refusing a turn: the daily message limit of %s is spent", limit)
    return RateLimitRefusal(
        limit=limit,
        reset_at=window.reset_at,
        retry_after_seconds=window.retry_after_seconds(
            now or datetime.now(timezone.utc)
        ),
    )
