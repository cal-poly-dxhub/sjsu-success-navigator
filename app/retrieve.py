"""Bedrock Knowledge Base retrieval: top-k, drop below the score floor, carry metadata out."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config

from settings import Settings

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
