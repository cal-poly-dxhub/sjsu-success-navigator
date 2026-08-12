"""The per-user daily message cap: the one control that bounds what ONE account spends.

WHY THIS EXISTS. Everything else fencing cost bounds the SERVICE. The stage throttle bounds
invocations started per second, the reserved concurrency bounds invocations running at once,
and the loop's deadline and iteration cap bound a single request. Not one of them can tell
two students apart, so a single signed-in account can sit comfortably inside all four of them
all day and run up an unbounded Bedrock bill on its own. An answered message costs roughly
$0.026; sixty of them is about $1.56, and that is the number this file makes true per person.

THE COUNTER IS NOT ADDRESSABLE FROM A REQUEST. The partition is `USER#<sub>` built from the
JWT claim API Gateway validated, and the sort key is a date from the server clock. There is
no field in the body - present, absent, or forged - that changes which counter a turn
increments or what it is compared against. That is the same property the history table has
(docs/accounts-and-storage.md, Storage) and it is inherited rather than re-implemented.

ATTEMPTS, NOT ANSWERS, and BEFORE THE GUARDRAIL. The check runs before the guardrail screen
and before the model loop, so a refused turn costs one DynamoDB write and nothing else - no
ApplyGuardrail text units, no Converse call, no retrieval. Counting successes instead would
mean the cheapest way to exceed the cap is to send requests that fail, which is not a cap.

A FIXED CALENDAR DAY IN UTC, not a sliding window. A sliding window needs a read before the
write, and its precision buys nothing for a spend guard: the question is "has this person
spent more than a day's worth of money", not "at what instant did the sixtieth message land".
UTC rather than a campus-local midnight because a local boundary needs a timezone database
inside the function, and the student never reads the UTC string anyway - the 429 carries the
reset INSTANT and the browser renders it in their own time.

IT FAILS OPEN. A DynamoDB fault that is not a condition failure lets the turn through, logged
at ERROR. This is the posture the rest of the request path already takes - a guardrail outage
continues, a failed history write still answers - and the reasoning is the same: the failure
is not attributable to the student in front of the screen, and the service-wide fences above
are still standing while it lasts. The alternative turns a DynamoDB blip into a total outage
of a product that receives crisis disclosures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitWindow:
    """One user's allowance period: which counter to touch and when it lets go.

    `key` is the counter's sort-key suffix, `reset_at` the instant the allowance refills, and
    `expires_at` the same instant as the epoch seconds DynamoDB's TTL reads. The last two are
    one moment in two formats on purpose - a counter for a window that has closed is dead, so
    the TTL that collects it and the time the student is told to come back are by construction
    the same number rather than two that could drift.
    """

    key: str
    reset_at: datetime
    expires_at: int

    def retry_after_seconds(self, now: datetime) -> int:
        """Whole seconds until the allowance refills, never below 1.

        Rounded UP, because Retry-After is a promise that waiting this long is enough: a
        truncated value sends a client back a fraction of a second early, to be refused
        again. The floor of 1 is for the same reason - a `Retry-After: 0` invites an
        immediate retry that cannot succeed.
        """
        remaining = (self.reset_at - now).total_seconds()
        if remaining <= 1:
            return 1
        return int(remaining) + (1 if remaining % 1 else 0)


def window_for(now: datetime) -> RateLimitWindow:
    """The UTC calendar day `now` falls in.

    The key is the date alone, so every message a user sends between one UTC midnight and the
    next addresses the same item. `now` is passed in rather than read here so the window math
    is testable without moving a clock.
    """
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
    """What a caller needs to refuse a turn: the cap, when it lifts, and how long that is."""

    limit: int
    reset_at: datetime
    retry_after_seconds: int

    @property
    def reset_at_iso(self) -> str:
        """The reset instant as ISO 8601 UTC, the format every other timestamp on this wire
        uses. The browser parses it and renders the student's own local time."""
        return self.reset_at.isoformat(timespec="seconds").replace("+00:00", "Z")

    @property
    def message(self) -> str:
        """The refusal as one plain sentence, for a caller with no clock of its own.

        The browser replaces this with the same sentence in local time (chatApi.ts), so this
        wording is what curl, the eval harness and a stale frontend see. It says the cap and
        that it lifts, and deliberately does not try to name a time: this string is UTC and a
        student reading "after 07:00" for their own 12:00 midnight would be worse than a
        sentence that does not guess.
        """
        return (
            f"You have reached your daily limit of {self.limit} messages. "
            "Your limit resets at midnight UTC."
        )


def claim_turn(*, store, user_id, client_id, settings, now=None):
    """Take this turn out of the user's daily allowance. None to continue, or a refusal.

    THE ONE ENTRY POINT, called before the guardrail and before anything billable. Three ways
    it returns None, and they are all "this turn is not the thing the cap is for":

      - the cap is off (unset or zero, the gate config.yaml documents),
      - the caller is an exempt app client (the eval harness's machine client),
      - the user had an allowance left, which has now been spent.

    `store` is the ConversationStore. `client_id` is the JWT claim, or None; the caller reads
    it out of the same validated claim set as `sub`, so an exemption cannot be self-declared.
    """
    limit = settings.daily_message_limit
    if limit <= 0:
        return None

    if client_id is not None and client_id in settings.rate_limit_exempt_client_ids:
        # The eval harness: 82 questions as one account at concurrency 3. Not logged per
        # request - it would be the loudest line in the log during a run and says nothing a
        # reader does not already know from the client id.
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
        # FAILS OPEN. See the module docstring: the service-wide fences are still up, and a
        # student cannot be refused their question over a fault that is not theirs. This log
        # line is the alarm, and it is at ERROR because an unmetered turn is a real cost
        # event rather than a degraded feature.
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
