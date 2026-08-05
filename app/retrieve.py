"""Bedrock Knowledge Base retrieval - camp's backend/services/retrieve.py.

Behaviour is camp's, unchanged: top-k Retrieve, drop anything below the score floor,
carry title/source_url/section out of each chunk's metadata. Two porting changes, both
about running on Lambda rather than under uvicorn:

  - the boto3 client is built ONCE at module scope, not per call. Camp built a client
    inside every request, which on Lambda throws away the warm container's connection
    pool on each invocation.
  - it carries an explicit retry config, so a Bedrock throttle is retried rather than
    surfacing to the student as a 502.

`section` is load-bearing downstream: cards.py keys its deprioritization and follow-up
presets off it, so it is read here even though nothing in this module uses it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config

from settings import Settings

# Module scope: built once per container, reused by every invocation. adaptive retries
# back off on throttling rather than failing the request.
_AGENT_RUNTIME_CLIENT = None


def _agent_runtime_client(region: str):
    global _AGENT_RUNTIME_CLIENT
    if _AGENT_RUNTIME_CLIENT is None:
        _AGENT_RUNTIME_CLIENT = boto3.client(
            "bedrock-agent-runtime",
            region_name=region,
            config=Config(
                retries={"max_attempts": 3, "mode": "adaptive"},
                read_timeout=20,
                connect_timeout=5,
            ),
        )
    return _AGENT_RUNTIME_CLIENT


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    score: float
    source_url: str | None
    title: str | None
    section: str | None
    s3_uri: str | None


def retrieve_chunks(query: str, settings: Settings) -> list[RetrievedChunk]:
    client = _agent_runtime_client(settings.bedrock_region)

    response = client.retrieve(
        knowledgeBaseId=settings.knowledge_base_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": settings.number_of_results,
            }
        },
    )

    chunks: list[RetrievedChunk] = []
    for result in response.get("retrievalResults", []):
        score = float(result.get("score", 0))
        if score < settings.retrieve_min_score:
            continue

        metadata: dict[str, Any] = result.get("metadata") or {}
        location = result.get("location") or {}
        s3_location = location.get("s3Location") or {}

        chunks.append(
            RetrievedChunk(
                text=(result.get("content") or {}).get("text", ""),
                score=score,
                source_url=metadata.get("source_url"),
                title=metadata.get("title"),
                section=metadata.get("section"),
                s3_uri=s3_location.get("uri"),
            )
        )

    return chunks
