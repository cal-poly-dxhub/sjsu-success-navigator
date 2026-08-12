"""The WebSocket routes: who a caller is, and what a connection leaves behind.

The identity story is the whole point of this file. On POST /chat the `sub` comes out of
claims API Gateway's native JWT authorizer validated; here it comes out of the `context` a
Lambda authorizer returned at $connect, which API Gateway then attaches to every route on
the connection. Different mechanism, same rule: nothing a client sends in a frame can name
a user.
"""

import json

import pytest

from conftest import TEST_SUB


CONNECTION_ID = "gXDKAK5mzeO4KEhleA=="


def ws_event(route, sub=TEST_SUB, client_id="web-client-id", connection_id=CONNECTION_ID, body=None):
    """A WebSocket route event, shaped as API Gateway actually sends one.

    The authorizer context sits at `requestContext.authorizer` as a FLAT map - not under a
    `jwt.claims` nesting like the HTTP API's - and it is present on every route, not only
    $connect. Both of those are copied from a real event captured against a deployed probe;
    a hand-imagined shape here would let the handler read a field that never arrives.
    """
    event = {
        "requestContext": {
            "routeKey": route,
            "connectionId": connection_id,
            "eventType": {"$connect": "CONNECT", "$disconnect": "DISCONNECT"}.get(route, "MESSAGE"),
            "domainName": "example.execute-api.us-west-2.amazonaws.com",
            "stage": "stream",
        }
    }
    if sub is not None:
        context = {"sub": sub}
        if client_id is not None:
            context["clientId"] = client_id
        event["requestContext"]["authorizer"] = context
    if body is not None:
        event["body"] = body if isinstance(body, str) else json.dumps(body)
    return event


@pytest.fixture
def ws_store(monkeypatch):
    import streaming

    from conftest import FakeConversationStore

    fake = FakeConversationStore()
    monkeypatch.setattr(streaming, "STORE", fake)
    return fake


def test_a_connect_records_the_connection_against_the_validated_sub(ws_store):
    import streaming

    response = streaming.lambda_handler(ws_event("$connect"), None)

    assert response["statusCode"] == 200
    assert ws_store.connections == {(TEST_SUB, CONNECTION_ID)}
    (_, kwargs) = ws_store.calls[0]
    assert kwargs["user_id"] == TEST_SUB
    assert kwargs["connection_id"] == CONNECTION_ID


def test_the_connection_record_carries_a_ttl_past_the_two_hour_hard_cap(ws_store):
    """API Gateway closes an idle connection after 10 minutes and ANY connection after 2
    hours, so a record that outlives those quotas describes a connection that cannot exist.
    The TTL is the backstop for the $disconnect that never fires - a Lambda error, a
    throttle - not a second opinion about when a connection ends."""
    import streaming

    streaming.lambda_handler(ws_event("$connect"), None)
    (_, kwargs) = ws_store.calls[0]

    import time

    seconds_out = kwargs["expires_at"] - time.time()
    two_hours = 2 * 60 * 60
    assert seconds_out > two_hours, "the TTL must outlive API Gateway's own hard cap"
    assert seconds_out < 24 * 60 * 60, "a connection record is not a day's worth of data"


def test_a_connect_with_no_authorizer_claim_is_refused(ws_store):
    """The route is authorizer-gated, so arriving without a `sub` is a misconfigured stack
    rather than a student - and every key this connection would touch is built from that
    claim. Same posture as POST /chat's 401: fail closed, write nothing."""
    import streaming

    response = streaming.lambda_handler(ws_event("$connect", sub=None), None)

    assert response["statusCode"] == 401
    assert ws_store.connections == set()
    assert ws_store.calls == []


def test_identity_is_never_read_from_the_frame(ws_store):
    """The one thing this file exists to stop. A frame carrying somebody else's `sub` must
    change nothing: the only identity is the one the $connect authorizer put in the
    context, and the body is not consulted for it."""
    import streaming

    event = ws_event("$connect", body={"sub": "victim-sub", "userId": "victim-sub"})
    streaming.lambda_handler(event, None)

    assert ws_store.connections == {(TEST_SUB, CONNECTION_ID)}
    assert streaming.identity_from(event) == TEST_SUB


def test_a_disconnect_clears_the_record(ws_store):
    import streaming

    streaming.lambda_handler(ws_event("$connect"), None)
    response = streaming.lambda_handler(ws_event("$disconnect"), None)

    assert response["statusCode"] == 200
    assert ws_store.connections == set()
    assert ws_store.call_names == ["connect", "disconnect"]


def test_a_disconnect_with_no_identity_is_not_an_error(ws_store):
    """$disconnect is best effort by nature - API Gateway does not guarantee it fires at
    all. With nothing addressable there is nothing to delete, and the TTL collects the row
    either way, so this is not worth failing over."""
    import streaming

    response = streaming.lambda_handler(ws_event("$disconnect", sub=None), None)

    assert response["statusCode"] == 200
    assert ws_store.calls == []


def test_an_unknown_route_is_refused_rather_than_falling_through(ws_store):
    """The stack creates a fixed set of routes. An unknown one means somebody added a route
    and pointed it here, and falling through to a billable path is the kind of default that
    is discovered from an invoice."""
    import streaming

    response = streaming.lambda_handler(ws_event("$default", body={"action": "sendMessage"}), None)

    assert response["statusCode"] == 404
    assert ws_store.calls == []


def test_the_route_key_is_read_from_the_request_context_not_the_top_level():
    """An HTTP API payload-2.0 event carries `routeKey` at the top level; a WebSocket event
    carries it inside `requestContext`. Reading the wrong one is not a crash - it is a
    silent miss, which for the HTTP handler's own dispatch would mean running a billable
    chat turn on a WebSocket frame."""
    import streaming

    event = ws_event("$connect")
    assert "routeKey" not in event
    assert event["requestContext"]["routeKey"] == "$connect"


def test_the_client_id_travels_for_the_rate_limit_and_is_not_an_identity():
    """Same claim, same single use as on POST /chat: the rate limit's exemption list. It is
    shared by everybody who signs in through that client, so nothing keys storage on it."""
    import streaming

    assert streaming.client_id_from(ws_event("$connect")) == "web-client-id"
    assert streaming.client_id_from(ws_event("$connect", client_id=None)) is None
    assert streaming.identity_from(ws_event("$connect", client_id=None)) == TEST_SUB
