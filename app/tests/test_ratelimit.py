"""The per-user daily message cap: the window, the refusal, and what it costs to be refused.

The acceptance property behind most of this file is one sentence: an over-limit request must
be refused BEFORE any Bedrock call. Not before the model loop - before the guardrail too,
which is a billed call of its own. So the assertions here are usually about what did NOT
happen, and the guardrail fake is the witness.

The concurrency test at the bottom is the one worth reading twice. It exercises the fake
store's compare-and-increment under a lock, which models DynamoDB's per-item serialisation;
test_history.py separately pins that the real store sends the conditional expression that
buys that guarantee. Neither can prove DynamoDB's behaviour without an account, and the pair
is what makes the claim checkable from here.
"""

import concurrent.futures
import json
from datetime import datetime, timedelta, timezone

import pytest

import handler
import ratelimit
from conftest import EXEMPT_CLIENT_ID, TEST_SUB, chat_event


def _body(response):
    return json.loads(response["body"])


def _ask(question="Where is tutoring?", **kwargs):
    return handler.lambda_handler(chat_event({"query": question}, **kwargs), None)


class _FakeBedrock:
    """The guardrail client, here purely to be caught not being called."""

    def __init__(self):
        self.calls = []

    def apply_guardrail(self, **kwargs):
        self.calls.append(kwargs)
        return {"action": "NONE"}


@pytest.fixture
def bedrock(monkeypatch):
    fake = _FakeBedrock()
    monkeypatch.setattr(handler, "_bedrock_client", lambda: fake)
    return fake


@pytest.fixture
def loop(monkeypatch):
    """A run_chat stand-in that counts model turns. Zero calls is the assertion.

    The titling call is stubbed out alongside it. Every allowed turn here starts a NEW
    conversation, so the real one would run - swallow its own failure against the stubbed
    boto3, log a warning, and leave the conversation with its fallback title. Harmless, and
    sixty of them buried in one test's output is noise that hides the assertion.
    """
    from models import ChatResponse

    calls = []

    def _run(request, settings, history=(), deadline=None, usage=None):
        calls.append(request)
        return ChatResponse(conversationalText="Peer Connections runs drop-in tutoring.")

    monkeypatch.setattr(handler, "run_chat", _run)
    monkeypatch.setattr(handler, "generate_title", lambda **kwargs: None)
    return calls


# --- the window ----------------------------------------------------------------


def test_the_window_is_the_utc_calendar_day():
    window = ratelimit.window_for(datetime(2026, 8, 12, 13, 45, tzinfo=timezone.utc))
    assert window.key == "2026-08-12"
    assert window.reset_at == datetime(2026, 8, 13, tzinfo=timezone.utc)


def test_every_moment_of_one_utc_day_shares_a_counter():
    """The first second and the last of a day address the SAME item. A key that changed
    inside the window would hand the user a fresh allowance partway through it."""
    first = ratelimit.window_for(datetime(2026, 8, 12, 0, 0, 0, tzinfo=timezone.utc))
    last = ratelimit.window_for(datetime(2026, 8, 12, 23, 59, 59, tzinfo=timezone.utc))
    assert first.key == last.key == "2026-08-12"
    assert first.expires_at == last.expires_at


def test_the_next_day_is_a_different_counter():
    today = ratelimit.window_for(datetime(2026, 8, 12, 23, 59, 59, tzinfo=timezone.utc))
    tomorrow = ratelimit.window_for(datetime(2026, 8, 13, 0, 0, 0, tzinfo=timezone.utc))
    assert today.key != tomorrow.key


def test_the_counter_expires_when_the_window_does():
    """`expiresAt` is the table's TTL attribute in epoch SECONDS, and it is the same instant
    the student is told to come back at - one moment in two formats, so they cannot drift."""
    window = ratelimit.window_for(datetime(2026, 8, 12, 6, 0, tzinfo=timezone.utc))
    assert window.expires_at == int(window.reset_at.timestamp())
    assert window.expires_at == int(datetime(2026, 8, 13, tzinfo=timezone.utc).timestamp())


def test_a_non_utc_clock_still_lands_in_the_right_window():
    """Late evening in a +14 zone is already the next day in UTC, and the counter follows
    UTC. The alternative would key the window on whatever timezone the caller's clock
    happened to carry, which is not a fixed window at all."""
    plus_fourteen = timezone(timedelta(hours=14))
    window = ratelimit.window_for(datetime(2026, 8, 13, 10, 0, tzinfo=plus_fourteen))
    assert window.key == "2026-08-12"


def test_retry_after_is_rounded_up_and_never_zero():
    """Retry-After is a promise that waiting that long is enough. A truncated value sends a
    client back early to be refused again; a zero invites an immediate retry that cannot
    work."""
    window = ratelimit.window_for(datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc))
    assert window.retry_after_seconds(datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc)) == 3600
    at_the_wire = datetime(2026, 8, 12, 23, 59, 59, 500000, tzinfo=timezone.utc)
    assert window.retry_after_seconds(at_the_wire) == 1


# --- the limit itself ----------------------------------------------------------


def test_a_request_under_the_limit_is_unchanged(store, bedrock, loop, daily_limit):
    daily_limit(60)
    response = _ask()

    assert response["statusCode"] == 200
    assert len(loop) == 1
    assert len(bedrock.calls) == 1


def test_exactly_at_the_limit_the_last_message_still_answers(
    store, bedrock, loop, daily_limit
):
    """THE BOUNDARY. With a cap of 60 a user gets 60 answers, not 59 and not 61 - the
    condition is `count < limit` against the count already spent, so the 60th message sees
    59 and passes. An off-by-one here is either a cap that silently under-delivers what it
    promises or one that lets an extra paid turn through every day."""
    daily_limit(60)

    for _ in range(60):
        assert _ask()["statusCode"] == 200

    assert len(loop) == 60

    refused = _ask()
    assert refused["statusCode"] == 429
    assert len(loop) == 60, "the 61st message must not reach the model"


def test_an_over_limit_request_is_refused_before_any_bedrock_call(
    store, bedrock, loop, daily_limit
):
    """The acceptance criterion, stated as the two calls that must not happen. The guardrail
    is on this list as well as the loop: ApplyGuardrail bills per 1,000-character text unit,
    so a check placed after it would still charge for every message an over-limit account
    sent."""
    daily_limit(1)
    assert _ask()["statusCode"] == 200
    bedrock.calls.clear()
    loop.clear()

    response = _ask()

    assert response["statusCode"] == 429
    assert bedrock.calls == [], "the guardrail must not be called for a refused turn"
    assert loop == [], "the model must not be called for a refused turn"


def test_a_refused_turn_writes_no_message(store, bedrock, loop, daily_limit):
    """A refused turn is not a turn. Nothing is appended, no header is touched, and no
    conversation appears in the student's sidebar for a message that was never sent."""
    daily_limit(1)
    _ask()
    store.calls.clear()

    _ask()

    assert store.call_names == ["allowance"]


def test_the_refusal_says_when_it_lifts(store, bedrock, loop, daily_limit):
    daily_limit(1)
    _ask()

    response = _ask()
    body = _body(response)

    assert body["limit"] == 1
    assert body["resetAt"].endswith("Z")
    assert body["retryAfterSeconds"] > 0
    assert response["headers"]["Retry-After"] == str(body["retryAfterSeconds"])
    # A plain sentence for a caller with no clock of its own. The browser rewrites it in
    # local time (frontend/src/lib/chatApi.ts).
    assert "daily limit of 1 messages" in body["error"]


def test_the_refusal_reset_is_the_next_utc_midnight(store, bedrock, loop, daily_limit):
    daily_limit(1)
    _ask()

    reset = datetime.fromisoformat(_body(_ask())["resetAt"].replace("Z", "+00:00"))

    assert (reset.hour, reset.minute, reset.second) == (0, 0, 0)
    assert reset > datetime.now(timezone.utc)


def test_a_second_user_has_their_own_allowance(store, bedrock, loop, daily_limit):
    """The counter is per PARTITION, and the partition is the caller's `sub`. One student
    exhausting their day cannot touch anybody else's."""
    daily_limit(1)
    assert _ask()["statusCode"] == 200
    assert _ask()["statusCode"] == 429

    other = _ask(sub="99999999-8888-7777-6666-555555555555")
    assert other["statusCode"] == 200


# --- what cannot influence the counter ------------------------------------------


def test_the_counter_is_keyed_on_the_jwt_sub_and_the_server_clock(
    store, bedrock, loop, daily_limit
):
    """Neither half of the counter's identity comes from the request. The partition is the
    validated claim and the sort key is today's date."""
    daily_limit(60)
    _ask()

    (_, claim), = [call for call in store.calls if call[0] == "allowance"]
    assert claim["user_id"] == TEST_SUB
    assert claim["window_key"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")


@pytest.mark.parametrize(
    "smuggled",
    [
        {"userId": "somebody-else"},
        {"sub": "somebody-else"},
        {"user_id": "somebody-else"},
        {"windowKey": "1999-01-01"},
        {"dailyMessageLimit": 100000},
        {"limit": 100000},
        {"rateLimit": {"exempt": True}},
    ],
)
def test_no_request_field_can_move_the_counter(
    store, bedrock, loop, daily_limit, smuggled
):
    """THE ACCEPTANCE CRITERION: the counter cannot be influenced by request content. Each
    body below is an attempt to name a different user, a different window, or a bigger cap.
    Every one of them is an unknown key that pydantic drops, and none of the three values
    they are reaching for is read from the body in the first place."""
    daily_limit(1)
    handler.lambda_handler(chat_event({"query": "hi", **smuggled}), None)
    store.calls.clear()

    response = handler.lambda_handler(chat_event({"query": "hi", **smuggled}), None)

    assert response["statusCode"] == 429
    (_, claim), = [call for call in store.calls if call[0] == "allowance"]
    assert claim["user_id"] == TEST_SUB
    assert claim["window_key"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")


# --- the gate and the exemption -------------------------------------------------


def test_a_limit_of_zero_disables_the_cap_entirely(store, bedrock, loop, daily_limit):
    """The gate config.yaml documents: absent or zero is off. Off means the write does not
    happen at all, not that it happens against a limit of zero - which would refuse
    everybody's first message."""
    daily_limit(0)

    for _ in range(5):
        assert _ask()["statusCode"] == 200

    assert "allowance" not in store.call_names


def test_the_machine_client_is_exempt(store, bedrock, loop, daily_limit):
    """The eval harness fires 82 questions as ONE account at concurrency 3. Keyed on the
    validated `client_id` claim, so the exemption belongs to the app client rather than to a
    username, and no browser can claim it."""
    daily_limit(1, exempt=(EXEMPT_CLIENT_ID,))

    for _ in range(10):
        assert _ask(client_id=EXEMPT_CLIENT_ID)["statusCode"] == 200

    assert "allowance" not in store.call_names
    assert len(loop) == 10


def test_the_exemption_does_not_leak_to_the_browsers_client(
    store, bedrock, loop, daily_limit
):
    """The same user, the same everything, through the web client instead. The exemption is
    the machine client's, not the account's."""
    daily_limit(1, exempt=(EXEMPT_CLIENT_ID,))
    assert _ask(client_id=EXEMPT_CLIENT_ID)["statusCode"] == 200
    assert _ask()["statusCode"] == 200
    assert _ask()["statusCode"] == 429


def test_a_token_with_no_client_id_falls_under_the_limit(
    store, bedrock, loop, daily_limit
):
    """An ID token carries `aud` and no `client_id`. The claim reads None, nothing matches
    the exemption list, and the cap applies - which is the safe direction."""
    daily_limit(1, exempt=(EXEMPT_CLIENT_ID,))
    assert _ask(client_id=None)["statusCode"] == 200
    assert _ask(client_id=None)["statusCode"] == 429


def test_nobody_is_exempt_when_the_list_is_empty(store, bedrock, loop, daily_limit):
    daily_limit(1, exempt=())
    assert _ask(client_id=EXEMPT_CLIENT_ID)["statusCode"] == 200
    assert _ask(client_id=EXEMPT_CLIENT_ID)["statusCode"] == 429


# --- failure posture ------------------------------------------------------------


def test_a_dynamodb_fault_lets_the_turn_through(store, bedrock, loop, daily_limit, caplog):
    """FAILS OPEN, deliberately, and this is the one test here asserting a hole rather than a
    fence. The service-wide throttle and the reserved concurrency are still standing while
    DynamoDB is broken, and refusing a student their question over a fault that is not theirs
    would turn a blip into an outage of a product that receives crisis disclosures. The
    ERROR log is the alarm."""
    daily_limit(60)
    store.fail_on.add("allowance")

    with caplog.at_level("ERROR"):
        response = _ask()

    assert response["statusCode"] == 200
    assert len(loop) == 1
    assert "daily message limit" in caplog.text


# --- concurrency ----------------------------------------------------------------


def test_two_concurrent_requests_at_the_limit_let_exactly_one_through(
    store, bedrock, loop, daily_limit
):
    """THE RACE THE CONDITIONAL WRITE EXISTS FOR. Two requests arrive together with one
    message of allowance left. A read-then-write would let both see the same count, both
    decide they were under the limit, and both spend a Bedrock turn on it - which at a
    concurrency of 20 is nineteen free messages a day, every day.

    What is being tested here is the handler against DynamoDB's item-level serialisation,
    modelled by the fake store's locked compare-and-increment. That the REAL store asks
    DynamoDB for that guarantee - one atomic ADD under `count < :limit`, no read - is pinned
    in test_history.py. Neither half can be checked against live DynamoDB from here.
    """
    daily_limit(10)
    store.counters[(TEST_SUB, datetime.now(timezone.utc).strftime("%Y-%m-%d"))] = 9

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        both = [pool.submit(_ask), pool.submit(_ask)]
        statuses = sorted(future.result()["statusCode"] for future in both)

    assert statuses == [200, 429]
    assert len(loop) == 1, "exactly one of the two may reach the model"


def test_concurrent_requests_never_exceed_the_limit_in_total(
    store, bedrock, loop, daily_limit
):
    """The same property at volume: twenty requests racing against a cap of five spend five
    turns, not six. The count the fake ends on is the assertion - an increment that escaped
    the condition would show up here as a counter past the limit."""
    daily_limit(5)
    window = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        statuses = [f.result()["statusCode"] for f in [pool.submit(_ask) for _ in range(20)]]

    assert statuses.count(200) == 5
    assert statuses.count(429) == 15
    assert len(loop) == 5
    assert store.counters[(TEST_SUB, window)] == 5
