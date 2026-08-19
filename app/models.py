"""The wire contract: camelCase aliases over what /chat and the read routes return.

ChatRequest deliberately carries no history and no user id; see docs/chat-service.md,
The request path.
"""

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


class PlaceCard(BaseModel):
    """One campus location, resolved server-side from a catalogue key the model wrote."""

    model_config = ConfigDict(populate_by_name=True)

    key: str
    name: str
    address: str
    directions_url: str = Field(alias="directionsUrl")
    map_image_url: str | None = Field(default=None, alias="mapImageUrl")


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


class EmailDraft(BaseModel):
    """A message to a human, assembled server-side and sent by nobody but the student."""

    model_config = ConfigDict(populate_by_name=True)

    to: str
    subject: str
    body: str


class ChatResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str | None = Field(default=None, alias="conversationId")
    title: str | None = None
    conversational_text: str = Field(alias="conversationalText")
    trailing_text: str | None = Field(default=None, alias="trailingText")
    statement_batches: list[StatementBatch] | None = Field(
        default=None,
        alias="statementBatches",
    )
    safety_handoff: SafetyHandoff | None = Field(default=None, alias="safetyHandoff")
    place: PlaceCard | None = None
    escalation: EmailDraft | None = None
    talk_to_person_available: bool = Field(
        default=True,
        alias="talkToPersonAvailable",
    )
    usage: TurnUsage | None = None

    # The storage projection, excluded from the wire.
    # See docs/chat-service.md, What a turn writes.
    raw_text: str = Field(default="", exclude=True)
    sources: dict[int, str] = Field(default_factory=dict, exclude=True)


# Validated because the value goes straight into a DynamoDB sort-key prefix.
CONVERSATION_ID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"


class ChatRequest(BaseModel):
    """One turn as a client may describe it. There is deliberately no history field."""

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
    """One stored message on its way to the browser: the DISPLAY projection."""

    model_config = ConfigDict(populate_by_name=True)

    role: Literal["user", "assistant"]
    text: str
    trailing_text: str | None = Field(default=None, alias="trailingText")
    cards: list[StatementCard] = Field(default_factory=list)
    safety_handoff: SafetyHandoff | None = Field(default=None, alias="safetyHandoff")
    place: PlaceCard | None = None
    escalation: EmailDraft | None = None
    created_at: str | None = Field(default=None, alias="createdAt")


class ConversationResponse(BaseModel):
    """GET /conversations/{conversationId}. An id that is not the caller's reads empty."""

    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(alias="conversationId")
    messages: list[ConversationMessage]


class ConversationRenameRequest(BaseModel):
    """PATCH /conversations/{conversationId} - the one field a rename may carry."""

    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(min_length=1)


class ConversationTitleResponse(BaseModel):
    """The result of a rename: the id and the title as STORED, after normalisation."""

    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(alias="conversationId")
    title: str


class ConversationDeleteResponse(BaseModel):
    """The result of a delete. `deletedMessages` is the count actually removed."""

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
