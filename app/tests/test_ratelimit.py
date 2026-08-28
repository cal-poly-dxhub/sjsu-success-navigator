"""The per-user daily message cap: the window, the refusal, and what a refusal costs.

Attempts rather than answers, a fixed UTC day, and it fails open.
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
    """A run_chat stand-in that counts model turns. Zero calls is the assertion."""
    from models import ChatResponse

    calls = []

    def _run(request, settings, history=(), deadline=None, usage=None):
        calls.append(request)
        return ChatResponse(conversationalText="Peer Connections runs drop-in tutoring.")

    monkeypatch.setattr(handler, "run_chat", _run)
    monkeypatch.setattr(handler, "generate_title", lambda **kwargs: None)
    return calls


def test_the_window_is_the_utc_calendar_day():
    window = ratelimit.window_for(datetime(2026, 8, 12, 13, 45, tzinfo=timezone.utc))
    assert window.key == "2026-08-12"
    assert window.reset_at == datetime(2026, 8, 13, tzinfo=timezone.utc)


def test_every_moment_of_one_utc_day_shares_a_counter():
    """The first second and the last of a day address the same item."""
    first = ratelimit.window_for(datetime(2026, 8, 12, 0, 0, 0, tzinfo=timezone.utc))
    last = ratelimit.window_for(datetime(2026, 8, 12, 23, 59, 59, tzinfo=timezone.utc))
    assert first.key == last.key == "2026-08-12"
    assert first.expires_at == last.expires_at


def test_the_next_day_is_a_different_counter():
    today = ratelimit.window_for(datetime(2026, 8, 12, 23, 59, 59, tzinfo=timezone.utc))
    tomorrow = ratelimit.window_for(datetime(2026, 8, 13, 0, 0, 0, tzinfo=timezone.utc))
    assert today.key != tomorrow.key


def test_the_counter_expires_when_the_window_does():
    """`expiresAt` is the TTL attribute in epoch seconds, and the same instant as the reset."""
    window = ratelimit.window_for(datetime(2026, 8, 12, 6, 0, tzinfo=timezone.utc))
    assert window.expires_at == int(window.reset_at.timestamp())
    assert window.expires_at == int(datetime(2026, 8, 13, tzinfo=timezone.utc).timestamp())


def test_a_non_utc_clock_still_lands_in_the_right_window():
    """Late evening in a +14 zone is already the next day in UTC, and the counter follows."""
    plus_fourteen = timezone(timedelta(hours=14))
    window = ratelimit.window_for(datetime(2026, 8, 13, 10, 0, tzinfo=plus_fourteen))
    assert window.key == "2026-08-12"


def test_retry_after_is_rounded_up_and_never_zero():
    """Retry-After is a promise that waiting that long is enough, so it rounds up."""
    window = ratelimit.window_for(datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc))
    assert window.retry_after_seconds(datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc)) == 3600
    at_the_wire = datetime(2026, 8, 12, 23, 59, 59, 500000, tzinfo=timezone.utc)
    assert window.retry_after_seconds(at_the_wire) == 1


def test_a_request_under_the_limit_is_unchanged(store, bedrock, loop, daily_limit):
    daily_limit(60)
    response = _ask()

    assert response["statusCode"] == 200
    assert len(loop) == 1
    assert len(bedrock.calls) == 1


def test_exactly_at_the_limit_the_last_message_still_answers(
    store, bedrock, loop, daily_limit
):
    """The boundary: a cap of 60 gets 60 answers, not 59 and not 61."""
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
    """The acceptance criterion as the two calls that must not happen: guardrail and model."""
    daily_limit(1)
    assert _ask()["statusCode"] == 200
    bedrock.calls.clear()
    loop.clear()

    response = _ask()

    assert response["statusCode"] == 429
    assert bedrock.calls == [], "the guardrail must not be called for a refused turn"
    assert loop == [], "the model must not be called for a refused turn"


def test_a_refused_turn_writes_no_message(store, bedrock, loop, daily_limit):
    """A refused turn is not a turn: nothing appended, no header touched, no usage reported."""
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
    # A plain sentence for a caller with no clock. The browser rewrites it in local time.
    assert "daily limit of 1 messages" in body["error"]


def test_the_refusal_reset_is_the_next_utc_midnight(store, bedrock, loop, daily_limit):
    daily_limit(1)
    _ask()

    reset = datetime.fromisoformat(_body(_ask())["resetAt"].replace("Z", "+00:00"))

    assert (reset.hour, reset.minute, reset.second) == (0, 0, 0)
    assert reset > datetime.now(timezone.utc)


def test_a_second_user_has_their_own_allowance(store, bedrock, loop, daily_limit):
    """The counter is per partition, and the partition is the caller's `sub`."""
    daily_limit(1)
    assert _ask()["statusCode"] == 200
    assert _ask()["statusCode"] == 429

    other = _ask(sub="99999999-8888-7777-6666-555555555555")
    assert other["statusCode"] == 200


def test_the_counter_is_keyed_on_the_jwt_sub_and_the_server_clock(
    store, bedrock, loop, daily_limit
):
    """Neither half of the counter's identity comes from the request."""
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
    """Not the query, not the conversation id, not a field invented for the attempt."""
    daily_limit(1)
    handler.lambda_handler(chat_event({"query": "hi", **smuggled}), None)
    store.calls.clear()

    response = handler.lambda_handler(chat_event({"query": "hi", **smuggled}), None)

    assert response["statusCode"] == 429
    (_, claim), = [call for call in store.calls if call[0] == "allowance"]
    assert claim["user_id"] == TEST_SUB
    assert claim["window_key"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_a_limit_of_zero_disables_the_cap_entirely(store, bedrock, loop, daily_limit):
    """The gate config.yaml documents: absent or zero is off, and off writes nothing."""
    daily_limit(0)

    for _ in range(5):
        assert _ask()["statusCode"] == 200

    assert "allowance" not in store.call_names


def test_the_machine_client_is_exempt(store, bedrock, loop, daily_limit):
    """The eval harness fires its whole set as one account, keyed on the validated client id."""
    daily_limit(1, exempt=(EXEMPT_CLIENT_ID,))

    for _ in range(10):
        assert _ask(client_id=EXEMPT_CLIENT_ID)["statusCode"] == 200

    assert "allowance" not in store.call_names
    assert len(loop) == 10


def test_the_exemption_does_not_leak_to_the_browsers_client(
    store, bedrock, loop, daily_limit
):
    """The same user through the web client: the exemption is the client, not the person."""
    daily_limit(1, exempt=(EXEMPT_CLIENT_ID,))
    assert _ask(client_id=EXEMPT_CLIENT_ID)["statusCode"] == 200
    assert _ask()["statusCode"] == 200
    assert _ask()["statusCode"] == 429


def test_a_token_with_no_client_id_falls_under_the_limit(
    store, bedrock, loop, daily_limit
):
    """An ID token carries `aud` and no `client_id`, so the caller falls under the limit."""
    daily_limit(1, exempt=(EXEMPT_CLIENT_ID,))
    assert _ask(client_id=None)["statusCode"] == 200
    assert _ask(client_id=None)["statusCode"] == 429


def test_nobody_is_exempt_when_the_list_is_empty(store, bedrock, loop, daily_limit):
    daily_limit(1, exempt=())
    assert _ask(client_id=EXEMPT_CLIENT_ID)["statusCode"] == 200
    assert _ask(client_id=EXEMPT_CLIENT_ID)["statusCode"] == 429


def test_a_dynamodb_fault_lets_the_turn_through(store, bedrock, loop, daily_limit, caplog):
    """Fails open, deliberately: the one test here asserting a hole rather than a wall."""
    daily_limit(60)
    store.fail_on.add("allowance")

    with caplog.at_level("ERROR"):
        response = _ask()

    assert response["statusCode"] == 200
    assert len(loop) == 1
    assert "daily message limit" in caplog.text


def test_two_concurrent_requests_at_the_limit_let_exactly_one_through(
    store, bedrock, loop, daily_limit
):
    """The race the conditional write exists for: two requests, one allowance left."""
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
    """The same property at volume: twenty requests against a cap of five spend five."""
    daily_limit(5)
    window = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        statuses = [f.result()["statusCode"] for f in [pool.submit(_ask) for _ in range(20)]]

    assert statuses.count(200) == 5
    assert statuses.count(429) == 15
    assert len(loop) == 5
    assert store.counters[(TEST_SUB, window)] == 5
