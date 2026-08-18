"""Canonical, serializable provenance records shared by structured and RAG tools."""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Evidence(BaseModel):
    """One actual source fragment used by a tool result or final conclusion."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    task_id: str | None = None
    source_type: Literal["structured", "unstructured"]
    file_id: str
    file_name: str
    sheet: str | None = None
    page_no: int | None = None
    chunk_id: str | None = None
    field: str | None = None
    record_key: str | None = None
    value_summary: str


def make_evidence_id(**parts: Any) -> str:
    """Build a stable identifier from normalized provenance, never random content."""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_evidence(**values: Any) -> dict[str, Any]:
    return Evidence(**values).model_dump()
