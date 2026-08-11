from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from usage import TurnUsage


class SourceAction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: Literal["source"] = "source"
    label: str


class FollowupAction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: Literal["followup"] = "followup"
    label: str
    prompt: str


StatementAction = Annotated[
    Union[SourceAction, FollowupAction],
    Field(discriminator="type"),
]


class StatementCard(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    body: str
    source_url: str = Field(alias="sourceUrl")
    actions: list[StatementAction]


class StatementBatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    cards: list[StatementCard]
    query: str | None = None
    created_at: int = Field(alias="createdAt")


class SafetyContact(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    detail: str
    href: str


class SafetyHandoff(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    headline: str
    body: str
    contacts: list[SafetyContact]


class ChatResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # The conversation this turn belongs to, minted by the server on the first turn and
    # echoed on every one after. The client's only job is to send it back; it never picks
    # one (docs/accounts-and-storage.md, Turn lifecycle). None when no turn was recorded -
    # a guardrail block, which never became a turn.
    conversation_id: str | None = Field(default=None, alias="conversationId")
    # The name this conversation was just given, present ONLY on the turn that created it
    # (app/titles.py). Additive and one-directional: the sidebar can show the real title
    # immediately instead of its own placeholder, and no other turn carries the field.
    # The server is still the authority - this is the same value a later GET /conversations
    # returns, arriving sooner.
    title: str | None = None
    conversational_text: str = Field(alias="conversationalText")
    # Prose the model wrote AFTER its cards, which renders below the card group rather than
    # above it. None for the ordinary reply that ends with its cards, and always None on a
    # safety turn, where the message is one bubble above the panel.
    trailing_text: str | None = Field(default=None, alias="trailingText")
    statement_batches: list[StatementBatch] | None = Field(
        default=None,
        alias="statementBatches",
    )
    safety_handoff: SafetyHandoff | None = Field(default=None, alias="safetyHandoff")
    talk_to_person_available: bool = Field(
        default=True,
        alias="talkToPersonAvailable",
    )
    # What this turn actually cost in billable units (app/usage.py). Additive, and the only
    # field here the student's answer does not depend on: it is what lets the cost panel
    # price THIS conversation from real token counts instead of a sample average. Present on
    # every turn the handler runs, including a guardrail block, which billed a screen. None
    # on a response built anywhere else, and an older client ignoring it loses nothing.
    usage: TurnUsage | None = None


# A conversation id is a ULID minted by history.new_ulid(): 26 characters of Crockford
# base32, which omits I, L, O and U. Validated because the value goes STRAIGHT INTO A SORT
# KEY - `MSG#<convId>#<ulid>` - so an id carrying a `#` would compose key prefixes the
# server did not intend. Within the sender's own partition, which is why this is a 400 for
# a malformed id rather than a security boundary: the boundary is the partition key, and
# that comes from the JWT. A well-formed id for a conversation that does not exist is not
# an error - it reads as empty, which is the doc's stated behaviour for a forged one.
CONVERSATION_ID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"


class ChatRequest(BaseModel):
    """One turn, as the client is allowed to describe it: a conversation and a message.

    THERE IS NO HISTORY FIELD, and its absence is the point of this model rather than an
    oversight. Client-supplied history is a prompt injection vector (docs/accounts-and-storage.md,
    Turn lifecycle): a forged assistant turn lets an attacker establish rules the model then
    treats as its own prior commitment. The server holds the transcript, so there is nothing
    here to forge.

    A `history` or `messages` key in the body is an unknown field and pydantic drops it -
    ignored, not sanitised, exactly as the doc says. No branch reads it, so there is no code
    path an attacker can reach by sending one. The old frontend may keep posting its
    transcript; the server simply has nowhere to put it.

    THERE IS NO USER ID EITHER, for the same reason turned up one level: `sub` is a claim in
    the JWT the API Gateway authorizer already validated, and a field a client fills in is a
    field a client can change. It would look like a harmless convenience.

    `followup` stays on the wire with no backend reader (docs/cards-v2.md, Tell me more): a
    follow-up click is an ordinary user turn and must produce a byte-identical model call.
    `sessionId` is gone: it was a client-chosen id nothing read, and `conversationId` is the
    same idea with the server choosing it. The frontend may keep sending it, to no effect.
    """

    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(
        default=None,
        alias="conversationId",
        pattern=CONVERSATION_ID_PATTERN,
    )
    followup: bool = False


class ConversationSummary(BaseModel):
    """One row of GET /conversations: a stored header, as the sidebar lists it."""

    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(alias="conversationId")
    title: str
    created_at: str | None = Field(default=None, alias="createdAt")
    last_activity_at: str | None = Field(default=None, alias="lastActivityAt")
    message_count: int = Field(default=0, alias="messageCount")


class ConversationListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conversations: list[ConversationSummary]


class ConversationMessage(BaseModel):
    """One stored message on its way to the browser: the DISPLAY projection.

    `cards` are the stored ones - URLs already resolved - re-validated through the same
    StatementCard the live turn returns, so a reopened conversation and a fresh one hand the
    renderer the identical shape. This is the projection the model never sees
    (docs/accounts-and-storage.md, Turn lifecycle): rendered cards are not fed back.
    """

    model_config = ConfigDict(populate_by_name=True)

    role: Literal["user", "assistant"]
    text: str
    cards: list[StatementCard] = Field(default_factory=list)
    created_at: str | None = Field(default=None, alias="createdAt")


class ConversationResponse(BaseModel):
    """GET /conversations/{conversationId}.

    An id that is not the caller's own is not an error and not a 403 - it is an empty
    `messages` list, because the partition it would have to read is not the one the JWT
    names (docs/accounts-and-storage.md, Turn lifecycle).
    """

    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(alias="conversationId")
    messages: list[ConversationMessage]


class ConversationRenameRequest(BaseModel):
    """PATCH /conversations/{conversationId} - the one field a rename may carry.

    THERE IS NO CONVERSATION ID IN THE BODY, and no user id, for the reason every other
    request model here says: the id comes from the validated path and the partition comes
    from the JWT claim. A body that could name either would be a body that could name
    somebody else's.

    The cap is the same `title_max_chars` the model titling is held to, so the sidebar's
    rows have one length limit rather than two. An over-cap name is a 400 rather than a
    silent truncation: it is the student's own words, and shortening them without saying so
    would show them a name they did not choose.
    """

    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(min_length=1)


class ConversationTitleResponse(BaseModel):
    """The result of a rename: the id and the title as STORED, after normalisation."""

    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(alias="conversationId")
    title: str


class ConversationDeleteResponse(BaseModel):
    """The result of a delete. `deletedMessages` is the count actually removed, which the
    browser ignores and a log line does not: it is how "delete removed every message" stops
    being a claim and becomes a number."""

    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(alias="conversationId")
    deleted_messages: int = Field(alias="deletedMessages")


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    kb_id: str = Field(alias="kbId")
    model_id: str = Field(alias="modelId")
    region: str
    aws_credentials: Literal["ok", "missing"] = Field(alias="awsCredentials")

    model_config = ConfigDict(populate_by_name=True)


class KbProbeResult(BaseModel):
    title: str | None = None
    source_url: str | None = Field(default=None, alias="sourceUrl")
    score: float | None = None
    section: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class KbHealthResponse(BaseModel):
    status: Literal["ok", "error"]
    query: str
    result_count: int = Field(alias="resultCount")
    top_results: list[KbProbeResult] = Field(alias="topResults")
    error: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class CardsHealthResponse(BaseModel):
    status: Literal["ok", "error"]
    query: str
    card_count: int = Field(alias="cardCount")
    cards: list[StatementCard]
    error: str | None = None

    model_config = ConfigDict(populate_by_name=True)
