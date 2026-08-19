"""The conversation store: the item shapes the doc fixes, and the one query a turn makes.

See docs/accounts-and-storage.md and docs/chat-service.md, Storage.
"""

import inspect
import re

import pytest

import history
from models import CONVERSATION_ID_PATTERN

_SUB = "11111111-2222-3333-4444-555555555555"
_CONV = "01J0000000000000000000000A"


class _ClientError(Exception):
    """Shaped like botocore's ClientError, because the error CODE is what the store reads."""

    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeTable:
    """Records calls and hands back canned Items. `raises_on` fails one operation."""

    def __init__(self, items=(), raises_on=None, pages=None):
        self.items = list(items)
        self.pages = None if pages is None else list(pages)
        self.raises_on = raises_on
        self.puts = []
        self.updates = []
        self.queries = []
        self.deletes = []
        self.batched_deletes = []

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

    def delete_item(self, **kwargs):
        self.deletes.append(kwargs)
        if self.raises_on == "delete_item":
            raise RuntimeError("ProvisionedThroughputExceeded")
        return {}

    def batch_writer(self):
        table = self

        class _Batch:
            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def delete_item(self, **kwargs):
                table.batched_deletes.append(kwargs)
                if table.raises_on == "batch_delete":
                    raise RuntimeError("ProvisionedThroughputExceeded")

        return _Batch()

    def query(self, **kwargs):
        self.queries.append(kwargs)
        if self.raises_on == "query":
            raise RuntimeError("ProvisionedThroughputExceeded")
        if self.pages is None:
            return {"Items": self.items}
        page = self.pages[len(self.queries) - 1]
        result = {"Items": page}
        if len(self.queries) < len(self.pages):
            result["LastEvaluatedKey"] = {"pk": "USER#x", "sk": page[-1]["sk"]}
        return result


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
    """The anti-drift pin between the two halves of the id contract: minted and accepted."""
    for _ in range(50):
        assert re.match(CONVERSATION_ID_PATTERN, history.new_ulid())


def test_a_ulid_sorts_by_time_even_when_its_random_half_does_not(monkeypatch):
    """The whole reason this is not a uuid4: the sort key is the message order."""
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
    """Otherwise a question and its answer would be ordered by their random halves."""
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
    """The isolation story in one assertion: the partition is not something a request names."""
    table.store.append_message(
        user_id="attacker", conversation_id=_CONV, role="user", text="x"
    )
    assert table.puts[0]["Item"]["pk"] == "USER#attacker"


def test_an_assistant_message_is_stored_as_the_model_wrote_it(table):
    """THE RECORD IS THE REPLY, not the halves it was rendered into."""
    reply = 'Two places can help.\n\n<card ref="1"><title>Writing Center</title></card>\n\nWhich one?'
    table.store.append_message(
        user_id=_SUB,
        conversation_id=_CONV,
        role="assistant",
        text=reply,
        sources={1: "https://www.sjsu.edu/writingcenter/index.php"},
    )
    assert table.puts[0]["Item"]["text"] == reply


def test_an_assistant_message_carries_the_sources_its_cards_cited(table):
    """The one thing in a reply the model could not have written: it never sees a URL."""
    table.store.append_message(
        user_id=_SUB,
        conversation_id=_CONV,
        role="assistant",
        text='<card ref="1"><title>Tutoring</title></card>',
        sources={1: "https://www.sjsu.edu/tutoring/index.php"},
    )
    assert table.puts[0]["Item"]["sources"] == {
        "1": "https://www.sjsu.edu/tutoring/index.php"
    }


def test_a_reply_that_cited_nothing_stores_no_sources_attribute(table):
    """An empty map would claim the model produced a card group that it did not."""
    table.store.append_message(
        user_id=_SUB, conversation_id=_CONV, role="assistant", text="Here you go.", sources={}
    )
    assert "sources" not in table.puts[0]["Item"]


def test_a_message_never_carries_the_rendered_cards_any_more(table):
    """The attribute is READ, for the rows already written with it, and never written."""
    table.store.append_message(
        user_id=_SUB,
        conversation_id=_CONV,
        role="assistant",
        text='<card ref="1"><title>Tutoring</title></card>',
        sources={1: "https://www.sjsu.edu/tutoring/index.php"},
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
    """The message is already durable when the counter is bumped, and a drifted count is
    repairable from the messages while the messages are not repairable from anything."""
    table.raises_on = "update_item"

    with caplog.at_level("ERROR"):
        sort_key = table.store.append_message(
            user_id=_SUB, conversation_id=_CONV, role="user", text="I need help"
        )

    assert sort_key, "the write returned normally"
    assert table.puts, "and the message reached the table"
    assert "conversation header" in caplog.text


def test_a_failed_message_write_is_not_swallowed(table):
    """The opposite direction, deliberately: the handler decides what a lost message costs."""
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
    # Strongly consistent, and the case it covers is the PREVIOUS turn, not this one.
    assert query["ConsistentRead"] is True


def test_the_context_projection_fetches_text_and_never_cards(table):
    """Two projections of the same query: the model is fed text, the browser gets the rest."""
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
    """You never read back your own write: the orchestrator appends this turn in memory."""
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
    """The only way one of these exists is a shape this code did not write."""
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


# --- the display reads ----------------------------------------------------------------


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
    """The doc's first access pattern: no owner attribute to filter on and none to forget."""
    table.items = [_header("01J0000000000000000000000A")]

    table.store.list_conversations(user_id=_SUB, limit=40)

    query = table.queries[0]
    assert query["ExpressionAttributeValues"][":pk"] == f"USER#{_SUB}"
    assert query["ExpressionAttributeValues"][":prefix"] == "CONV#"
    assert query["KeyConditionExpression"] == "pk = :pk AND begins_with(sk, :prefix)"
    assert query["Limit"] == 40
    # Newest FIRST, so the limit takes the newest conversations rather than the oldest.
    assert query["ScanIndexForward"] is False
    # A student who sends a turn and reloads must see it.
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
    """The one way that happens is a turn whose user write failed but whose reply landed."""
    table.items = [_header("01J0000000000000000000000A", title="")]

    assert table.store.list_conversations(user_id=_SUB, limit=40)[0].title


def test_a_zero_list_window_asks_dynamodb_nothing(table):
    assert table.store.list_conversations(user_id=_SUB, limit=0) == []
    assert table.queries == []


def test_the_display_read_fetches_the_sources_the_context_read_refuses_to(table):
    """The whole difference between the two projections, in one assertion."""
    table.items = [
        {
            "sk": f"MSG#{_CONV}#01",
            "role": "assistant",
            "text": '<card ref="1"><title>Peer Connections</title></card>',
            "sources": {"1": "https://sjsu.edu/peer"},
            "createdAt": "2026-08-10T18:04:00Z",
        }
    ]

    messages = table.store.conversation_messages(
        user_id=_SUB, conversation_id=_CONV, limit=60
    )

    query = table.queries[0]
    assert "#sources" in query["ExpressionAttributeNames"]
    assert "#sources" in query["ProjectionExpression"]
    assert query["ExpressionAttributeValues"][":prefix"] == f"MSG#{_CONV}#"
    assert messages[0].sources == {1: "https://sjsu.edu/peer"}
    assert messages[0].created_at == "2026-08-10T18:04:00Z"


def test_the_display_read_still_serves_a_row_written_before_the_record_kept_model_text(
    table,
):
    """The rows already on the table: nothing writes `cards` any more and the read serves them."""
    table.items = [
        {
            "sk": f"MSG#{_CONV}#01",
            "role": "assistant",
            "text": "Peer Connections runs drop-in tutoring.",
            "cards": [{"id": "c1", "sourceUrl": "https://sjsu.edu/peer"}],
        }
    ]

    messages = table.store.conversation_messages(
        user_id=_SUB, conversation_id=_CONV, limit=60
    )

    assert messages[0].cards == [{"id": "c1", "sourceUrl": "https://sjsu.edu/peer"}]
    assert messages[0].sources == {}, "that row never recorded any"


def test_an_unreadable_source_ref_costs_one_link_and_not_the_conversation(table, caplog):
    """Same posture as the unreadable-item skip: one link, never the conversation."""
    table.items = [
        {
            "sk": f"MSG#{_CONV}#01",
            "role": "assistant",
            "text": "Here you go.",
            "sources": {"1": "https://sjsu.edu/peer", "one": "https://sjsu.edu/other"},
        }
    ]

    with caplog.at_level("WARNING"):
        messages = table.store.conversation_messages(
            user_id=_SUB, conversation_id=_CONV, limit=60
        )

    assert messages[0].sources == {1: "https://sjsu.edu/peer"}
    assert "stored source ref" in caplog.text


def test_the_display_read_comes_back_oldest_first(table):
    """DynamoDB is asked for the newest `limit` descending, then reversed, because a long
    conversation must show the end a student is returning to."""
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


# --- titling, renaming and deleting -------------------------------------------------------


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
    """An UpdateItem with no condition is an upsert, which would mint an empty conversation."""
    table.store.set_generated_title(user_id=_SUB, conversation_id=_CONV, title="A title")
    assert "attribute_exists(sk)" in table.updates[0]["ConditionExpression"]


def test_a_generated_title_never_overwrites_a_student_chosen_name(table):
    """The promise to a student who renamed a chat, written as a condition rather than left
    to the ordering that happens to hold today."""
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
    """A throttled rename must not be reported to the student as "no such conversation"."""
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


def test_a_delete_removes_every_message_before_the_header(table):
    table.items = [{"sk": f"MSG#{_CONV}#01"}, {"sk": f"MSG#{_CONV}#02"}]

    assert table.store.delete_conversation(user_id=_SUB, conversation_id=_CONV) == 2

    assert [d["Key"]["sk"] for d in table.batched_deletes] == [
        f"MSG#{_CONV}#01",
        f"MSG#{_CONV}#02",
    ]
    assert [d["Key"]["sk"] for d in table.deletes] == [f"CONV#{_CONV}"], (
        "the header is the last thing deleted"
    )


def test_a_delete_that_fails_midway_leaves_the_header(monkeypatch):
    """THE ORDERING IS THE WHOLE DESIGN: this leaves an empty but VISIBLE conversation, and
    the other order leaves an orphaned transcript nothing can reach."""
    fake = _FakeTable(items=[{"sk": f"MSG#{_CONV}#01"}], raises_on="batch_delete")
    store = history.ConversationStore("chat-history-test")
    monkeypatch.setattr(store, "_table_resource", lambda: fake)

    with pytest.raises(RuntimeError):
        store.delete_conversation(user_id=_SUB, conversation_id=_CONV)

    assert fake.deletes == [], "the header was deleted despite a failed message delete"


def test_a_delete_pages_through_a_long_conversation(monkeypatch):
    """Query returns at most 1 MB, so stopping at the first page would orphan the tail."""
    pages = [
        [{"sk": f"MSG#{_CONV}#01"}, {"sk": f"MSG#{_CONV}#02"}],
        [{"sk": f"MSG#{_CONV}#03"}],
    ]
    fake = _FakeTable(pages=pages)
    store = history.ConversationStore("chat-history-test")
    monkeypatch.setattr(store, "_table_resource", lambda: fake)

    assert store.delete_conversation(user_id=_SUB, conversation_id=_CONV) == 3
    assert len(fake.queries) == 2
    assert "ExclusiveStartKey" in fake.queries[1]
    assert len(fake.batched_deletes) == 3


def test_a_delete_only_ever_addresses_the_callers_own_partition(table):
    table.items = [{"sk": f"MSG#{_CONV}#01"}]
    table.store.delete_conversation(user_id="attacker", conversation_id=_CONV)

    assert table.queries[0]["ExpressionAttributeValues"][":pk"] == "USER#attacker"
    assert table.batched_deletes[0]["Key"]["pk"] == "USER#attacker"
    assert table.deletes[0]["Key"]["pk"] == "USER#attacker"


def test_deleting_a_conversation_that_is_not_there_is_a_no_op(table):
    """Idempotent by construction: a forged id addresses a prefix holding nothing."""
    table.items = []
    assert table.store.delete_conversation(user_id=_SUB, conversation_id=_CONV) == 0
    assert table.batched_deletes == []


def test_the_title_cap_travels_with_the_store(monkeypatch):
    """One number for both things that can name a conversation."""
    fake = _FakeTable()
    store = history.ConversationStore("chat-history-test", title_max_chars=20)
    monkeypatch.setattr(store, "_table_resource", lambda: fake)

    store.append_message(user_id=_SUB, conversation_id=_CONV, role="user", text="x" * 50)

    title = fake.updates[0]["ExpressionAttributeValues"][":title"]
    assert len(title) == 20 and title.endswith("…")


# --- the rate-limit counter --------------------------------------------------------------


def test_the_allowance_claim_is_one_atomic_conditional_write(table):
    """THE WHOLE RACE GUARANTEE, in one request: the compare and the increment are the same
    operation, and the condition is evaluated against the value the item holds then."""
    assert (
        table.store.claim_message_allowance(
            user_id=_SUB, window_key="2026-08-12", limit=60, expires_at=1786579200
        )
        is True
    )

    assert len(table.updates) == 1, "one write, and no read before it"
    assert table.queries == [], "the count is never read back to be compared in Python"
    update = table.updates[0]
    assert "ADD #count :one" in update["UpdateExpression"]
    assert update["ConditionExpression"] == (
        "attribute_not_exists(#count) OR #count < :limit"
    )
    assert update["ExpressionAttributeValues"][":limit"] == 60
    assert update["ExpressionAttributeValues"][":one"] == 1


def test_the_counter_is_its_own_item_in_the_users_partition(table):
    """Same partition as the caller's conversations, built from the JWT claim like every key."""
    table.store.claim_message_allowance(
        user_id=_SUB, window_key="2026-08-12", limit=60, expires_at=1786579200
    )

    key = table.updates[0]["Key"]
    assert key == {"pk": f"USER#{_SUB}", "sk": "RATE#DAY#2026-08-12"}
    assert not key["sk"].startswith("CONV#") and not key["sk"].startswith("MSG#")


def test_the_counter_carries_the_tables_ttl_attribute(table):
    """`expiresAt`, epoch seconds, with if_not_exists so the window's end is fixed once."""
    table.store.claim_message_allowance(
        user_id=_SUB, window_key="2026-08-12", limit=60, expires_at=1786579200
    )

    update = table.updates[0]
    assert "SET #expiresAt = if_not_exists(#expiresAt, :expiresAt)" in update["UpdateExpression"]
    assert update["ExpressionAttributeNames"]["#expiresAt"] == "expiresAt"
    assert update["ExpressionAttributeValues"][":expiresAt"] == 1786579200


def test_count_goes_through_expression_attribute_names(table):
    """`count` is a DynamoDB reserved word, and used bare it fails at RUNTIME."""
    table.store.claim_message_allowance(
        user_id=_SUB, window_key="2026-08-12", limit=60, expires_at=1786579200
    )
    assert table.updates[0]["ExpressionAttributeNames"]["#count"] == "count"


def test_a_spent_allowance_is_a_false_not_an_exception(monkeypatch):
    """The condition failing is the guard working, so it comes back as a value."""
    fake = _FakeTable(raises_on="condition")
    store = history.ConversationStore("chat-history-test")
    monkeypatch.setattr(store, "_table_resource", lambda: fake)

    assert (
        store.claim_message_allowance(
            user_id=_SUB, window_key="2026-08-12", limit=60, expires_at=1786579200
        )
        is False
    )


def test_any_other_failure_on_the_allowance_write_still_raises(monkeypatch):
    """A throttled counter write must not read as "the student is over their limit"."""
    fake = _FakeTable(raises_on="update_item")
    store = history.ConversationStore("chat-history-test")
    monkeypatch.setattr(store, "_table_resource", lambda: fake)

    with pytest.raises(RuntimeError):
        store.claim_message_allowance(
            user_id=_SUB, window_key="2026-08-12", limit=60, expires_at=1786579200
        )


# --- the connection records that are gone (was app/streaming.py) --------------------------


def test_the_store_has_no_connection_api_at_all():
    """THE INVERSION OF FOUR TESTS THAT USED TO PIN THESE METHODS EXIST.

    The WebSocket transport is gone, so the fourth sort-key prefix in this partition
    (`CONN#<connectionId>`) has nothing to write it and nothing to read it. This is not a
    tidy-up: a store that still offered `open_connection` would offer a way to put an item
    in a student's partition that no read here will ever surface and no TTL policy was
    re-argued for, and the next person to find the method would reasonably assume something
    still consumed it.

    Asserted on the CLASS rather than by grepping the file, so a method reintroduced under
    any docstring fails."""
    assert not hasattr(history.ConversationStore, "open_connection")
    assert not hasattr(history.ConversationStore, "close_connection")
    assert "CONN#" not in inspect.getsource(history), (
        "the connection sort-key prefix is still written somewhere in app/history.py"
    )


def test_a_connection_record_is_invisible_to_every_read_in_the_module(table):
    """KEPT UNCHANGED FROM WHEN THERE WERE CONNECTION RECORDS TO BE INVISIBLE TO. The
    assertion is about the READS - that each one names its prefix rather than scanning the
    partition - and that property outlives the item kind that made it worth writing down."""
    table.store.list_conversations(user_id=_SUB, limit=10)
    table.store.conversation_messages(user_id=_SUB, conversation_id=_CONV, limit=10)
    table.store.recent_messages(user_id=_SUB, conversation_id=_CONV, limit=10)

    for query in table.queries:
        prefix = query["ExpressionAttributeValues"][":prefix"]
        assert prefix in ("CONV#", f"MSG#{_CONV}#")
        assert not prefix.startswith("CONN#")


# --- the escalation draft --------------------------------------------------------------


def test_an_assistant_message_carries_its_escalation_draft(table):
    draft = {"to": "sjsucares@sjsu.edu", "subject": "S", "body": "B"}
    table.store.append_message(
        user_id=_SUB,
        conversation_id=_CONV,
        role="assistant",
        text="That one needs a person.",
        escalation=draft,
    )
    assert table.puts[0]["Item"]["escalation"] == draft


def test_a_reply_with_no_offer_stores_no_escalation_attribute(table):
    """Same rule as the cards beside it: an absent attribute says the turn made no offer."""
    table.store.append_message(
        user_id=_SUB, conversation_id=_CONV, role="assistant", text="Here you go."
    )
    assert "escalation" not in table.puts[0]["Item"]


def test_the_display_read_fetches_the_draft_and_the_context_read_does_not(table):
    """The two projections again: a draft is display-only, exactly like a card."""
    table.items = [
        {
            "sk": f"MSG#{_CONV}#01",
            "role": "assistant",
            "text": "That one needs a person.",
            "escalation": {"to": "sjsucares@sjsu.edu", "subject": "S", "body": "B"},
            "createdAt": "2026-08-10T18:04:00Z",
        }
    ]

    messages = table.store.conversation_messages(
        user_id=_SUB, conversation_id=_CONV, limit=60
    )
    assert messages[0].escalation == {
        "to": "sjsucares@sjsu.edu",
        "subject": "S",
        "body": "B",
    }
    assert "#escalation" in table.queries[0]["ProjectionExpression"]

    table.queries.clear()
    table.store.recent_messages(user_id=_SUB, conversation_id=_CONV, limit=12)
    assert "escalation" not in table.queries[0]["ProjectionExpression"]


def test_a_stored_message_with_no_draft_reads_back_as_none(table):
    table.items = [
        {
            "sk": f"MSG#{_CONV}#01",
            "role": "assistant",
            "text": "Peer Connections runs drop-in tutoring.",
            "createdAt": "2026-08-10T18:04:00Z",
        }
    ]

    messages = table.store.conversation_messages(
        user_id=_SUB, conversation_id=_CONV, limit=60
    )

    assert messages[0].escalation is None


# --- the location card --------------------------------------------------------------------


def test_an_assistant_message_carries_its_location_card(table):
    place = {
        "key": "career-center",
        "name": "Career Center",
        "address": "Clark Hall, 1st floor, room 140",
        "directionsUrl": "https://www.google.com/maps/dir/?api=1&destination=Clark+Hall",
        "embedUrl": None,
    }
    table.store.append_message(
        user_id=_SUB,
        conversation_id=_CONV,
        role="assistant",
        text="Clark Hall, first floor.",
        place=place,
    )
    assert table.puts[0]["Item"]["place"] == place


def test_a_reply_with_no_location_stores_no_place_attribute(table):
    table.store.append_message(
        user_id=_SUB, conversation_id=_CONV, role="assistant", text="Here you go."
    )
    assert "place" not in table.puts[0]["Item"]


def test_the_display_read_fetches_the_location_and_the_context_read_does_not(table):
    """The two projections one more time: a location is display-only, exactly like a card."""
    table.items = [
        {
            "sk": f"MSG#{_CONV}#01",
            "role": "assistant",
            "text": "Clark Hall, first floor.",
            "place": {"key": "career-center", "name": "Career Center"},
            "createdAt": "2026-08-10T18:04:00Z",
        }
    ]

    messages = table.store.conversation_messages(
        user_id=_SUB, conversation_id=_CONV, limit=60
    )
    assert messages[0].place == {"key": "career-center", "name": "Career Center"}
    assert "#place" in table.queries[0]["ProjectionExpression"]

    table.queries.clear()
    table.store.recent_messages(user_id=_SUB, conversation_id=_CONV, limit=12)
    assert "place" not in table.queries[0]["ProjectionExpression"]


def test_a_stored_message_with_no_location_reads_back_as_none(table):
    table.items = [
        {
            "sk": f"MSG#{_CONV}#01",
            "role": "assistant",
            "text": "Peer Connections runs drop-in tutoring.",
            "createdAt": "2026-08-10T18:04:00Z",
        }
    ]

    messages = table.store.conversation_messages(
        user_id=_SUB, conversation_id=_CONV, limit=60
    )

    assert messages[0].place is None
