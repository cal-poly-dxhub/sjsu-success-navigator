from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


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

    conversational_text: str = Field(alias="conversationalText")
    statement_batches: list[StatementBatch] | None = Field(
        default=None,
        alias="statementBatches",
    )
    safety_handoff: SafetyHandoff | None = Field(default=None, alias="safetyHandoff")
    talk_to_person_available: bool = Field(
        default=True,
        alias="talkToPersonAvailable",
    )


class ChatHistoryMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, alias="sessionId")
    followup: bool = False
    history: list[ChatHistoryMessage] | None = Field(default=None, max_length=12)


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
