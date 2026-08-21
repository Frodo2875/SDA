"""Canonical, serializable provenance records shared by structured and RAG tools."""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    block_id: str | None = None
    table: str | None = None
    cell: str | None = None
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    field: str | None = None
    record_key: str | None = None
    value_summary: str


def make_evidence_id(**parts: Any) -> str:
    """Build a stable identifier from normalized provenance, never random content."""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_evidence(**values: Any) -> dict[str, Any]:
    evidence = Evidence(**values).model_dump()
    if not is_evidence_locatable(evidence):
        raise ValueError("Evidence 缺少可定位的原文引用")
    for key in ("block_id", "table", "cell", "bbox", "confidence"):
        if evidence[key] is None:
            evidence.pop(key)
    return evidence


def is_evidence_locatable(evidence: dict[str, Any] | Evidence) -> bool:
    """Accept both Evidence 2.0 locators and the complete legacy locator shape."""
    data = evidence.model_dump() if isinstance(evidence, Evidence) else evidence
    if not data.get("file_id"):
        return False
    if data.get("source_type") == "structured":
        return bool(
            (data.get("table") and data.get("cell"))
            or (
                data.get("sheet")
                and data.get("field")
                and data.get("record_key")
            )
        )
    return bool(data.get("block_id") or data.get("chunk_id"))
