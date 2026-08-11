"""The conversation store: the item shapes the doc fixes, and the one query a turn makes.

Nothing here reaches DynamoDB. What is being pinned is the request this code SENDS - the
keys, the projection, the consistency, the direction - because every one of those is a
property no unit test could recover after the fact from a table nobody can create without
an account, and several of them (the key schema above all) are immutable once there is data
on the table.
"""

import re

import pytest

import history
from models import CONVERSATION_ID_PATTERN

_SUB = "11111111-2222-3333-4444-555555555555"
_CONV = "01J0000000000000000000000A"


class _ClientError(Exception):
    """Shaped like botocore's ClientError, because the error CODE is what the store reads.

    The suite stubs boto3 out entirely (tests/conftest.py), so botocore.exceptions does not
    exist here - and that is the same reason history.py matches on the code rather than
    importing the class. This stand-in is that contract written down: an exception carrying
    `response["Error"]["Code"]`."""

    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeTable:
    """Records calls and hands back canned Items. `raises_on` fails one operation."""

    def __init__(self, items=(), raises_on=None):
        self.items = list(items)
        self.raises_on = raises_on
        self.puts = []
        self.updates = []
        self.queries = []

    def put_item(self, **kwargs):
        self.puts.append(kwargs)
        if self.raises_on == "put_item":
            raise RuntimeError("ProvisionedThroughputExceeded")
        return {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        if self.raises_on == "update_item":
            raise RuntimeError("ProvisionedThroughputExceeded")
        if self.raises_on == "condition":
            raise _ClientError("ConditionalCheckFailedException")
        return {}

    def query(self, **kwargs):
        self.queries.append(kwargs)
        if self.raises_on == "query":
            raise RuntimeError("ProvisionedThroughputExceeded")
        return {"Items": self.items}


@pytest.fixture
def table(monkeypatch):
    fake = _FakeTable()
    store = history.ConversationStore("chat-history-test")
    monkeypatch.setattr(store, "_table_resource", lambda: fake)
    fake.store = store
    return fake


def _item(role, text, sort_key):
    return {"sk": sort_key, "role": role, "text": text}


# --- ids ---------------------------------------------------------------------------------


def test_a_ulid_is_the_shape_a_conversation_id_is_validated_against():
    """The anti-drift pin between the two halves of the id contract: history.new_ulid mints
    them and models.CONVERSATION_ID_PATTERN is what an incoming one is checked against, so a
    server-minted id that its own validator rejected would be a conversation nobody could
    continue."""
    for _ in range(50):
        assert re.match(CONVERSATION_ID_PATTERN, history.new_ulid())


def test_a_ulid_sorts_by_time_even_when_its_random_half_does_not(monkeypatch):
    """The whole reason this is not a uuid4. Messages are ordered by their sort key and
    nothing else, so an id that sorts arbitrarily shuffles a conversation. The randomness is
    forced to its extremes here, so the ordering can only be coming from the timestamp."""
    monkeypatch.setattr(history, "_last_ulid_int", 0)
    clock = [1_700_000_000.0]
    monkeypatch.setattr(history.time, "time", lambda: clock[0])
    randbits = [(1 << 80) - 1, 0]
    monkeypatch.setattr(history.secrets, "randbits", lambda bits: randbits.pop(0))

    earlier = history.new_ulid()
    clock[0] += 1.0
    later = history.new_ulid()

    assert earlier < later


def test_two_ids_minted_in_one_millisecond_still_increase(monkeypatch):
    """Otherwise the order of a question and its answer would be decided by the random half
    of the id - a coin toss, on the rare turn that lands inside one millisecond."""
    monkeypatch.setattr(history, "_last_ulid_int", 0)
    monkeypatch.setattr(history.time, "time", lambda: 1_700_000_000.0)

    ids = [history.new_ulid() for _ in range(200)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


# --- writes ------------------------------------------------------------------------------


def test_a_message_is_one_item_with_the_docs_keys(table):
    sort_key = table.store.append_message(
        user_id=_SUB, conversation_id=_CONV, role="user", text="where do I get tutoring?"
    )

    item = table.puts[0]["Item"]
    assert item["pk"] == f"USER#{_SUB}"
    assert item["sk"] == sort_key
    assert sort_key.startswith(f"MSG#{_CONV}#")
    assert re.match(CONVERSATION_ID_PATTERN, sort_key.rsplit("#", 1)[1])
    assert item["role"] == "user"
    assert item["text"] == "where do I get tutoring?"
    assert item["createdAt"].endswith("Z")


def test_the_partition_key_is_built_from_the_sub_and_nothing_else(table):
    """The isolation story for this table in one assertion: the partition is not something
    a request can name, so a forged conversation id reads as an empty conversation belonging
    to the forger."""
    table.store.append_message(
        user_id="attacker", conversation_id=_CONV, role="user", text="x"
    )
    assert table.puts[0]["Item"]["pk"] == "USER#attacker"


def test_an_assistant_message_carries_its_resolved_cards(table):
    cards = [{"id": "card-1", "sourceUrl": "https://www.sjsu.edu/tutoring/index.php"}]
    table.store.append_message(
        user_id=_SUB, conversation_id=_CONV, role="assistant", text="Here you go.", cards=cards
    )
    assert table.puts[0]["Item"]["cards"] == cards


def test_a_reply_with_no_cards_stores_no_cards_attribute(table):
    """An empty list would claim the model produced a card group that it did not."""
    table.store.append_message(
        user_id=_SUB, conversation_id=_CONV, role="assistant", text="Here you go.", cards=[]
    )
    assert "cards" not in table.puts[0]["Item"]


def test_the_header_counter_is_an_atomic_add(table):
    """Not a read-modify-write: two turns in flight would each write the count they read."""
    table.store.append_message(
        user_id=_SUB, conversation_id=_CONV, role="user", text="where do I get tutoring?"
    )

    update = table.updates[0]
    assert update["Key"] == {"pk": f"USER#{_SUB}", "sk": f"CONV#{_CONV}"}
    assert "ADD #messageCount :one" in update["UpdateExpression"]
    assert update["ExpressionAttributeValues"][":one"] == 1
    assert "#lastActivityAt = :now" in update["UpdateExpression"]
    # Reserved words are why every attribute goes through a name placeholder.
    assert set(update["ExpressionAttributeNames"]) >= {
        "#createdAt",
        "#lastActivityAt",
        "#messageCount",
    }


def test_the_first_user_message_names_the_conversation_and_no_later_one_renames_it(table):
    table.store.append_message(
        user_id=_SUB, conversation_id=_CONV, role="user", text="where do I get tutoring?"
    )
    table.store.append_message(
        user_id=_SUB, conversation_id=_CONV, role="assistant", text="Peer Connections."
    )

    first, second = table.updates
    assert first["ExpressionAttributeValues"][":title"] == "where do I get tutoring?"
    assert "#title = if_not_exists(#title, :title)" in first["UpdateExpression"]
    assert "createdAt = if_not_exists" in first["UpdateExpression"].replace("#", "")
    assert ":title" not in second["ExpressionAttributeValues"], (
        "an assistant reply is not a title"
    )


def test_a_long_first_message_is_truncated_into_a_title(table):
    table.store.append_message(
        user_id=_SUB, conversation_id=_CONV, role="user", text="help " * 200
    )
    title = table.updates[0]["ExpressionAttributeValues"][":title"]
    assert len(title) <= 80
    assert title.endswith("…")


def test_a_header_failure_does_not_lose_the_message(table, caplog):
    """The message is already durable when the counter is bumped. A drifted count is
    repairable from the messages; the messages are not repairable from anything."""
    table.raises_on = "update_item"

    with caplog.at_level("ERROR"):
        sort_key = table.store.append_message(
            user_id=_SUB, conversation_id=_CONV, role="user", text="I need help"
        )

    assert sort_key, "the write returned normally"
    assert table.puts, "and the message reached the table"
    assert "conversation header" in caplog.text


def test_a_failed_message_write_is_not_swallowed(table):
    """The opposite direction, and deliberately: the handler decides whether a lost message
    is worth failing the turn over (it is not), but it cannot decide that if this returns
    quietly."""
    table.raises_on = "put_item"
    with pytest.raises(RuntimeError):
        table.store.append_message(
            user_id=_SUB, conversation_id=_CONV, role="user", text="I need help"
        )


# --- the read ----------------------------------------------------------------------------


def test_the_context_read_is_one_descending_limited_consistent_query(table):
    table.store.recent_messages(user_id=_SUB, conversation_id=_CONV, limit=12)

    query = table.queries[0]
    assert len(table.queries) == 1, "one query per turn, no paging, no second lookup"
    assert query["ExpressionAttributeValues"] == {
        ":pk": f"USER#{_SUB}",
        ":prefix": f"MSG#{_CONV}#",
    }
    assert query["KeyConditionExpression"] == "pk = :pk AND begins_with(sk, :prefix)"
    assert query["ScanIndexForward"] is False, "newest first, so the Limit is the window"
    assert query["Limit"] == 12
    # Strongly consistent, and the case it covers is the PREVIOUS turn, not this one: two
    # quick messages against an eventually consistent read can miss the last assistant
    # reply, which silently loses a turn.
    assert query["ConsistentRead"] is True


def test_the_context_projection_fetches_text_and_never_cards(table):
    """Two projections of the same query (the doc). The model is fed original message text;
    rendered cards are for a display read, and the cheapest way to guarantee they are never
    fed back is not to fetch them."""
    table.store.recent_messages(user_id=_SUB, conversation_id=_CONV, limit=12)

    query = table.queries[0]
    assert query["ProjectionExpression"] == "sk, #role, #text"
    assert "cards" not in query["ProjectionExpression"]


def test_the_read_comes_back_oldest_first(table):
    table.items = [
        _item("assistant", "third", "MSG#C#0003"),
        _item("user", "second", "MSG#C#0002"),
        _item("assistant", "first", "MSG#C#0001"),
    ]
    messages = table.store.recent_messages(user_id=_SUB, conversation_id=_CONV, limit=12)
    assert [m.text for m in messages] == ["first", "second", "third"]


def test_the_message_this_turn_just_wrote_is_excluded(table):
    """You never read back your own write: the orchestrator appends the current message in
    memory, so including it here would say it twice. The extra row asked for is the slot it
    occupies - asking for `limit` and then dropping it would quietly shorten the window."""
    table.items = [
        _item("user", "the message just written", "MSG#C#0003"),
        _item("assistant", "second", "MSG#C#0002"),
        _item("user", "first", "MSG#C#0001"),
    ]
    messages = table.store.recent_messages(
        user_id=_SUB, conversation_id=_CONV, limit=2, exclude_sort_key="MSG#C#0003"
    )

    assert table.queries[0]["Limit"] == 3
    assert [m.text for m in messages] == ["first", "second"]


def test_the_window_is_the_newest_n_after_the_exclusion(table):
    table.items = [
        _item("user", "just written", "MSG#C#0004"),
        _item("assistant", "third", "MSG#C#0003"),
        _item("user", "second", "MSG#C#0002"),
        _item("assistant", "first", "MSG#C#0001"),
    ]
    messages = table.store.recent_messages(
        user_id=_SUB, conversation_id=_CONV, limit=2, exclude_sort_key="MSG#C#0004"
    )
    assert [m.text for m in messages] == ["second", "third"]


def test_an_unreadable_item_is_skipped_rather_than_failing_the_turn(table, caplog):
    """The only way one of these exists is a shape this code did not write. Refusing to
    answer over a stray item would be a worse outcome than answering with less context."""
    table.items = [
        _item("assistant", "second", "MSG#C#0003"),
        {"sk": "MSG#C#0002", "role": "system", "text": "not a role we write"},
        {"sk": "MSG#C#0001b", "role": "user", "text": "   "},
        _item("user", "first", "MSG#C#0001"),
    ]
    with caplog.at_level("WARNING"):
        messages = table.store.recent_messages(
            user_id=_SUB, conversation_id=_CONV, limit=12
        )

    assert [m.text for m in messages] == ["first", "second"]
    assert "unreadable history item" in caplog.text


def test_a_zero_window_asks_dynamodb_nothing(table):
    assert table.store.recent_messages(user_id=_SUB, conversation_id=_CONV, limit=0) == []
    assert table.queries == []


# --- the display reads -------------------------------------------------------------------
#
# The other half of the doc's two projections. Same items, same partition, a different
# ProjectionExpression - and these are the ones that DO fetch cards, which is precisely why
# they are a separate method from the context read rather than a flag on it.


def _header(conversation_id, **attrs):
    item = {
        "sk": f"CONV#{conversation_id}",
        "title": "Where is tutoring?",
        "createdAt": "2026-08-10T18:00:00Z",
        "lastActivityAt": "2026-08-10T18:04:00Z",
        "messageCount": 4,
    }
    item.update(attrs)
    return item


def test_the_conversation_list_is_one_query_on_the_users_own_partition(table):
    """The doc's first access pattern. There is no owner attribute to filter on and none to
    forget: the partition key is built from the JWT claim, so 'list my conversations' and
    'list only mine' are the same query."""
    table.items = [_header("01J0000000000000000000000A")]

    table.store.list_conversations(user_id=_SUB, limit=40)

    query = table.queries[0]
    assert query["ExpressionAttributeValues"][":pk"] == f"USER#{_SUB}"
    assert query["ExpressionAttributeValues"][":prefix"] == "CONV#"
    assert query["KeyConditionExpression"] == "pk = :pk AND begins_with(sk, :prefix)"
    assert query["Limit"] == 40
    # Newest FIRST, so the limit takes the newest conversations rather than the oldest.
    assert query["ScanIndexForward"] is False
    # A student who sends a turn and reloads must see it: an eventually consistent read can
    # miss the header that turn just created.
    assert query["ConsistentRead"] is True


def test_a_listed_conversation_carries_what_a_sidebar_row_needs(table):
    table.items = [_header("01J0000000000000000000000A")]

    listed = table.store.list_conversations(user_id=_SUB, limit=40)[0]

    assert listed.conversation_id == "01J0000000000000000000000A"
    assert listed.title == "Where is tutoring?"
    assert listed.last_activity_at == "2026-08-10T18:04:00Z"
    assert listed.message_count == 4
    assert isinstance(listed.message_count, int), "a Decimal here would fail json.dumps"


def test_the_list_is_ordered_by_last_activity(table):
    """Most recent means the last one the student typed in, not the last one they started."""
    table.items = [
        _header("01J0000000000000000000000C", lastActivityAt="2026-08-09T09:00:00Z"),
        _header("01J0000000000000000000000B", lastActivityAt="2026-08-11T09:00:00Z"),
        _header("01J0000000000000000000000A", lastActivityAt="2026-08-10T09:00:00Z"),
    ]

    listed = table.store.list_conversations(user_id=_SUB, limit=40)

    assert [c.conversation_id[-1] for c in listed] == ["B", "A", "C"]


def test_a_header_with_no_title_still_gets_a_row(table):
    """The one way that happens is a turn whose user write failed and whose assistant write
    created the header. That conversation is still the student's, and a blank row would be
    less legible than a named one."""
    table.items = [_header("01J0000000000000000000000A", title="")]

    assert table.store.list_conversations(user_id=_SUB, limit=40)[0].title


def test_a_zero_list_window_asks_dynamodb_nothing(table):
    assert table.store.list_conversations(user_id=_SUB, limit=0) == []
    assert table.queries == []


def test_the_display_read_fetches_the_cards_the_context_read_refuses_to(table):
    """The whole difference between the two projections, in one assertion."""
    table.items = [
        {
            "sk": f"MSG#{_CONV}#01",
            "role": "assistant",
            "text": "Peer Connections runs drop-in tutoring.",
            "cards": [{"id": "c1", "sourceUrl": "https://sjsu.edu/peer"}],
            "createdAt": "2026-08-10T18:04:00Z",
        }
    ]

    messages = table.store.conversation_messages(
        user_id=_SUB, conversation_id=_CONV, limit=60
    )

    query = table.queries[0]
    assert "#cards" in query["ExpressionAttributeNames"]
    assert "#cards" in query["ProjectionExpression"]
    assert query["ExpressionAttributeValues"][":prefix"] == f"MSG#{_CONV}#"
    assert messages[0].cards == [{"id": "c1", "sourceUrl": "https://sjsu.edu/peer"}]
    assert messages[0].created_at == "2026-08-10T18:04:00Z"


def test_the_display_read_comes_back_oldest_first(table):
    """DynamoDB is asked for the newest `limit` (descending), because a conversation longer
    than the cap should show its END - then the page is reversed, because that is the order
    a transcript is read in."""
    table.items = [
        _item("assistant", "second", f"MSG#{_CONV}#02"),
        _item("user", "first", f"MSG#{_CONV}#01"),
    ]

    messages = table.store.conversation_messages(
        user_id=_SUB, conversation_id=_CONV, limit=60
    )

    assert table.queries[0]["ScanIndexForward"] is False
    assert [m.text for m in messages] == ["first", "second"]


def test_a_message_with_no_cards_reads_back_as_no_cards(table):
    table.items = [_item("user", "where is tutoring?", f"MSG#{_CONV}#01")]

    messages = table.store.conversation_messages(
        user_id=_SUB, conversation_id=_CONV, limit=60
    )

    assert messages[0].cards == []


def test_an_unreadable_item_is_skipped_by_the_display_read_too(table, caplog):
    table.items = [
        _item("assistant", "fine", f"MSG#{_CONV}#02"),
        {"sk": f"MSG#{_CONV}#01", "role": "system", "text": "not ours"},
    ]

    with caplog.at_level("WARNING"):
        messages = table.store.conversation_messages(
            user_id=_SUB, conversation_id=_CONV, limit=60
        )

    assert [m.text for m in messages] == ["fine"]
    assert "unreadable history item" in caplog.text


def test_a_zero_display_window_asks_dynamodb_nothing(table):
    assert (
        table.store.conversation_messages(user_id=_SUB, conversation_id=_CONV, limit=0)
        == []
    )
    assert table.queries == []


# --- titling and renaming -------------------------------------------------------


def test_a_generated_title_replaces_the_first_message_one(table):
    table.store.set_generated_title(
        user_id=_SUB, conversation_id=_CONV, title="Financial aid appeal deadline"
    )

    update = table.updates[0]
    assert update["Key"] == {"pk": f"USER#{_SUB}", "sk": f"CONV#{_CONV}"}
    assert update["ExpressionAttributeValues"][":title"] == "Financial aid appeal deadline"
    assert "if_not_exists" not in update["UpdateExpression"], (
        "the whole point is to overwrite the fallback title the first message wrote"
    )


def test_a_generated_title_cannot_create_a_header(table):
    """An UpdateItem with no condition is an upsert. Without this, titling a conversation
    whose writes all failed would mint a header with a title and no messages under it - a
    row in the sidebar that opens empty."""
    table.store.set_generated_title(user_id=_SUB, conversation_id=_CONV, title="A title")
    assert "attribute_exists(sk)" in table.updates[0]["ConditionExpression"]


def test_a_generated_title_never_overwrites_a_student_chosen_name(table):
    """The promise this feature makes to a student who renamed a chat, written as a
    condition rather than as an ordering: no automatic titling can take their name away.

    Today the ordering alone would do it (titling runs on the first turn, a rename cannot
    have happened yet), but that is an accident of when things run and this is the
    property."""
    table.store.set_generated_title(user_id=_SUB, conversation_id=_CONV, title="A title")
    condition = table.updates[0]["ConditionExpression"]
    assert "attribute_not_exists(#userTitled)" in condition
    assert table.updates[0]["ExpressionAttributeNames"]["#userTitled"] == "userTitled"


def test_a_failed_condition_is_a_false_not_an_exception(monkeypatch):
    fake = _FakeTable(raises_on="condition")
    store = history.ConversationStore("chat-history-test")
    monkeypatch.setattr(store, "_table_resource", lambda: fake)

    assert store.set_generated_title(user_id=_SUB, conversation_id=_CONV, title="x") is False
    assert store.rename_conversation(user_id=_SUB, conversation_id=_CONV, title="x") is False


def test_any_other_dynamodb_failure_still_raises(monkeypatch):
    """A throttled rename must not be reported to the student as "no such conversation".
    Only the one named condition is swallowed; everything else reaches the handler's 502."""
    fake = _FakeTable(raises_on="update_item")
    store = history.ConversationStore("chat-history-test")
    monkeypatch.setattr(store, "_table_resource", lambda: fake)

    with pytest.raises(RuntimeError):
        store.rename_conversation(user_id=_SUB, conversation_id=_CONV, title="x")


def test_a_rename_marks_the_header_user_titled(table):
    table.store.rename_conversation(
        user_id=_SUB, conversation_id=_CONV, title="Aid appeal"
    )

    update = table.updates[0]
    assert update["ExpressionAttributeValues"][":title"] == "Aid appeal"
    assert update["ExpressionAttributeValues"][":true"] is True
    assert update["ExpressionAttributeNames"]["#userTitled"] == "userTitled"
    assert update["ConditionExpression"] == "attribute_exists(sk)", (
        "a rename of a forged id must not create a header in the caller's own partition"
    )


def test_the_title_cap_travels_with_the_store(monkeypatch):
    """One number for both things that can name a conversation. A store built with the
    configured cap truncates the fallback title to it; the model title is held to the same
    value in app/titles.py."""
    fake = _FakeTable()
    store = history.ConversationStore("chat-history-test", title_max_chars=20)
    monkeypatch.setattr(store, "_table_resource", lambda: fake)

    store.append_message(user_id=_SUB, conversation_id=_CONV, role="user", text="x" * 50)

    title = fake.updates[0]["ExpressionAttributeValues"][":title"]
    assert len(title) == 20 and title.endswith("…")
