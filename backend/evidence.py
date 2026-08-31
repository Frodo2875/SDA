"""Canonical, serializable provenance records shared by structured and RAG tools."""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    """One source fragment in the backwards-compatible Evidence 2/3 envelope."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    evidence_version: Literal["2.0", "3.0"] = "2.0"
    task_id: str | None = None
    source_type: Literal["structured", "unstructured"]
    locator_type: Literal[
        "excel",
        "text_pdf",
        "visual_pdf",
        "image",
        "visual_table",
        "handwriting",
        "word",
    ] | None = None
    file_id: str
    file_name: str
    sheet: str | None = None
    page_no: int | None = None
    image_no: int | None = Field(default=None, ge=1)
    chunk_id: str | None = None
    block_id: str | None = None
    region_id: str | None = None
    table: str | None = None
    cell: str | None = None
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    handwriting_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    recognition_type: Literal["printed", "handwritten", "mixed", "unknown"] | None = None
    source_parser: str | None = None
    source_model: str | None = None
    field: str | None = None
    record_key: str | None = None
    value_summary: str
    text_excerpt: str | None = None
    score: float | None = None
    key_field_type: Literal["name", "student_id", "phone", "amount", "date"] | None = None
    review_required: bool = False
    review_reason: str | None = None
    safe_for_high_impact: bool | None = None
    safe_for_identity_match: bool | None = None
    conflict_sources: list[dict[str, Any]] = Field(default_factory=list)
    evidence_status: Literal["supported", "review_required", "conflict"] = "supported"


def make_evidence_id(**parts: Any) -> str:
    """Build a stable identifier from normalized provenance, never random content."""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_evidence(**values: Any) -> dict[str, Any]:
    """Validate and persist an Evidence 3 record in the existing V2 store."""
    values.setdefault("evidence_version", "3.0")
    values.setdefault("locator_type", _infer_locator_type(values))
    if values.get("recognition_type") == "handwritten" and values.get(
        "handwriting_confidence"
    ) is None:
        values["handwriting_confidence"] = values.get("confidence")
    if values.get("conflict_sources"):
        values["review_required"] = True
        values["evidence_status"] = "conflict"
    elif values.get("review_required"):
        values["evidence_status"] = "review_required"
    evidence = Evidence(**values).model_dump()
    if not is_evidence_locatable(evidence):
        raise ValueError("Evidence 缺少可定位的原文引用")
    # Import lazily to keep the canonical schema independent from persistence.
    # The opaque ID must be resolvable by a later frontend HTTP request.
    from backend import database

    database.save_evidence_location(evidence)
    if evidence.get("locator_type") in {"excel", "text_pdf", "word", None}:
        # Preserve the byte-for-byte Evidence 2 response contract consumed by
        # V1-V3 tools. The persisted JSON still carries the V3 envelope.
        legacy_keys = (
            "evidence_id", "task_id", "source_type", "file_id", "file_name",
            "sheet", "page_no", "chunk_id", "block_id", "table", "cell",
            "bbox", "confidence", "field", "record_key", "value_summary",
        )
        legacy = {key: evidence[key] for key in legacy_keys}
        for key in ("block_id", "table", "cell", "bbox", "confidence"):
            if legacy[key] is None:
                legacy.pop(key)
        return legacy
    for key in (
        "locator_type", "image_no", "chunk_id", "block_id", "region_id",
        "table", "cell", "row_index", "column_index", "bbox", "confidence",
        "handwriting_confidence", "recognition_type", "source_parser",
        "source_model", "text_excerpt", "score", "key_field_type",
        "review_reason", "safe_for_high_impact", "safe_for_identity_match",
    ):
        if evidence[key] is None:
            evidence.pop(key)
    return evidence


def serialize_evidence(evidence: dict[str, Any] | Evidence) -> dict[str, Any]:
    """Return one JSON-safe canonical envelope for legacy and visual Evidence."""
    model = evidence if isinstance(evidence, Evidence) else Evidence.model_validate(evidence)
    return model.model_dump(mode="json", exclude_none=True)


def deserialize_evidence(payload: str | dict[str, Any]) -> Evidence:
    """Load persisted Evidence 2.0 or Evidence 3.0 without schema migration."""
    values = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(values, dict):
        raise ValueError("Evidence payload 必须是 JSON object")
    return Evidence.model_validate(values)


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
    return bool(
        data.get("block_id")
        or data.get("chunk_id")
        or (data.get("region_id") and data.get("bbox"))
        or (data.get("table") and data.get("cell") and data.get("bbox"))
    )


def _infer_locator_type(values: dict[str, Any]) -> str | None:
    if values.get("source_type") == "structured":
        return "excel"
    if values.get("table") and values.get("cell") and values.get("bbox"):
        return "visual_table"
    if values.get("recognition_type") == "handwritten":
        return "handwriting"
    if values.get("image_no") is not None:
        return "image"
    file_name = str(values.get("file_name") or "").lower()
    if file_name.endswith(".pdf"):
        return "visual_pdf" if values.get("region_id") or values.get("bbox") else "text_pdf"
    if file_name.endswith((".doc", ".docx")):
        return "word"
    return None
