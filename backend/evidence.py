"""Canonical, serializable provenance records shared by structured and RAG tools."""

import hashlib
import json
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        "presentation",
        "txt",
        "json",
        "csv",
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
    line_number: int | None = Field(default=None, ge=1)
    json_path: str | None = Field(default=None, min_length=1, max_length=1024)
    slide_number: int | None = Field(default=None, ge=1)
    row_number: int | None = Field(default=None, ge=1)
    column_name: str | None = Field(default=None, min_length=1, max_length=256)
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
    trust_level: Literal["untrusted_document_data"] | None = None
    instruction_authority: Literal["none"] | None = None
    approval_authority: Literal["none"] | None = None
    can_trigger_tool: bool | None = None
    can_change_tool_risk: bool | None = None
    can_approve: bool | None = None
    detected_untrusted_patterns: list[str] = Field(default_factory=list)


EvidenceAuthority = Literal["official", "academic", "general", "unknown"]
EvidenceFreshness = Literal[
    "published_at_available", "retrieval_time_only", "not_applicable"
]
UnifiedSourceType = Literal["LOCAL", "WEB", "URL"]


class UnifiedEvidence(BaseModel):
    """Evidence 4.0 envelope shared by local, Web Search and Direct URL sources."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=128)
    evidence_version: Literal["4.0"] = "4.0"
    source_type: UnifiedSourceType
    content: str = Field(min_length=1, max_length=50_000)
    authority: EvidenceAuthority = "unknown"
    freshness: EvidenceFreshness
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Evidence 3.0 local provenance remains available without renaming fields.
    legacy_source_type: Literal["structured", "unstructured"] | None = None
    task_id: str | None = None
    locator_type: str | None = None
    file_id: str | None = None
    file_name: str | None = None
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
    line_number: int | None = Field(default=None, ge=1)
    json_path: str | None = None
    slide_number: int | None = Field(default=None, ge=1)
    row_number: int | None = Field(default=None, ge=1)
    column_name: str | None = None
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    handwriting_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    recognition_type: str | None = None
    source_parser: str | None = None
    source_model: str | None = None
    field: str | None = None
    record_key: str | None = None
    value_summary: str | None = None
    text_excerpt: str | None = None
    score: float | None = None
    review_required: bool = False
    review_reason: str | None = None
    evidence_status: str | None = None
    key_field_type: str | None = None
    safe_for_high_impact: bool | None = None
    safe_for_identity_match: bool | None = None
    conflict_sources: list[dict[str, Any]] = Field(default_factory=list)
    detected_untrusted_patterns: list[str] = Field(default_factory=list)

    # Web provenance is explicit and never inferred by the model.
    url: str | None = Field(default=None, max_length=4096)
    domain: str | None = Field(default=None, max_length=253)
    title: str | None = Field(default=None, max_length=500)
    publisher: str | None = Field(default=None, max_length=500)
    published_at: str | None = Field(default=None, max_length=128)
    retrieved_at: str | None = Field(default=None, max_length=128)
    trust_level: Literal[
        "untrusted_document_data", "untrusted_web_data"
    ] | None = None
    instruction_authority: Literal["none"] | None = None
    approval_authority: Literal["none"] | None = None
    can_trigger_tool: bool | None = None
    can_change_tool_risk: bool | None = None
    can_approve: bool | None = None

    @model_validator(mode="after")
    def validate_source_locator(self) -> "UnifiedEvidence":
        if self.source_type == "LOCAL":
            if not self.file_id or not self.file_name or not self.legacy_source_type:
                raise ValueError("LOCAL Evidence 缺少本地文件来源")
            if self.freshness != "not_applicable":
                raise ValueError("LOCAL Evidence freshness 必须为 not_applicable")
            return self
        if not self.url or not self.domain or not self.retrieved_at:
            raise ValueError("Web Evidence 缺少 URL、domain 或 retrieved_at")
        if self.trust_level != "untrusted_web_data":
            raise ValueError("Web Evidence 必须标记为不可信外部数据")
        if any((self.can_trigger_tool, self.can_change_tool_risk, self.can_approve)):
            raise ValueError("Web Evidence 不能获得 Tool 或审批权限")
        return self


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
        "line_number", "json_path", "slide_number", "row_number", "column_name",
    ):
        if evidence[key] is None:
            evidence.pop(key)
    return evidence


def upgrade_local_evidence(
    evidence: dict[str, Any] | Evidence,
) -> UnifiedEvidence:
    """Deterministically adapt a real Evidence 2/3 record into Evidence 4.0."""
    legacy = evidence if isinstance(evidence, Evidence) else Evidence.model_validate(evidence)
    values = legacy.model_dump(exclude_none=True)
    content = str(legacy.text_excerpt or legacy.value_summary).strip()
    if not content:
        raise ValueError("LOCAL Evidence 缺少真实内容")
    passthrough = {
        key: value
        for key, value in values.items()
        if key in UnifiedEvidence.model_fields
        and key not in {
            "evidence_version", "source_type", "legacy_source_type", "content",
            "authority", "freshness", "metadata", "trust_level",
            "instruction_authority", "approval_authority", "can_trigger_tool",
            "can_change_tool_risk", "can_approve",
        }
    }
    return UnifiedEvidence(
        **passthrough,
        evidence_version="4.0",
        source_type="LOCAL",
        legacy_source_type=legacy.source_type,
        content=content[:50_000],
        authority="unknown",
        freshness="not_applicable",
        metadata={"legacy_evidence_version": legacy.evidence_version},
        trust_level=legacy.trust_level or "untrusted_document_data",
        instruction_authority=legacy.instruction_authority or "none",
        approval_authority=legacy.approval_authority or "none",
        can_trigger_tool=False,
        can_change_tool_risk=False,
        can_approve=False,
    )


def build_web_evidence(
    record: dict[str, Any] | BaseModel,
    *,
    source_type: Literal["WEB", "URL"],
    retrieved_at: str,
    max_content_chars: int = 12_000,
) -> UnifiedEvidence:
    """Create Evidence 4.0 only from provider or fetched-page output."""
    values = (
        record.model_dump(mode="json")
        if isinstance(record, BaseModel)
        else dict(record)
    )
    metadata = dict(values.get("metadata") or {})
    raw_content = values.get("snippet") if source_type == "WEB" else values.get("content")
    content = str(raw_content or "").strip()
    if not content:
        raise ValueError("Web Evidence 缺少来源正文")
    bounded_content = content[:max(1, min(50_000, int(max_content_chars)))]
    url = str(values.get("url") or "").strip()
    domain = str(values.get("domain") or urlsplit(url).hostname or "").casefold()
    if not url or not domain:
        raise ValueError("Web Evidence 缺少有效 URL 来源")
    published_at = _explicit_text(
        values.get("published_at"),
        metadata.get("published_at"),
        metadata.get("datePublished"),
    )
    publisher = _explicit_text(values.get("publisher"), metadata.get("publisher"))
    authority = _explicit_authority(metadata.get("authority"))
    evidence_metadata = {
        **metadata,
        "content_truncated": len(content) > len(bounded_content),
    }
    if values.get("canonical_url"):
        evidence_metadata["canonical_url"] = values["canonical_url"]
    source_model = _explicit_text(values.get("source"))
    return UnifiedEvidence(
        evidence_id=make_evidence_id(
            source_type=source_type,
            url=url,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        ),
        source_type=source_type,
        content=bounded_content,
        value_summary=bounded_content,
        title=_explicit_text(values.get("title")),
        url=url,
        domain=domain,
        publisher=publisher,
        published_at=published_at,
        retrieved_at=str(retrieved_at),
        authority=authority,
        freshness=(
            "published_at_available" if published_at else "retrieval_time_only"
        ),
        metadata=evidence_metadata,
        source_model=source_model,
        trust_level="untrusted_web_data",
        instruction_authority="none",
        approval_authority="none",
        can_trigger_tool=False,
        can_change_tool_risk=False,
        can_approve=False,
    )


def unify_evidence_chain(
    evidence: list[dict[str, Any]],
    *,
    promote_local: bool,
) -> list[dict[str, Any]]:
    """Validate Web Evidence and optionally promote legacy local records."""
    unified: list[dict[str, Any]] = []
    for item in evidence:
        source_type = str(item.get("source_type") or "")
        if source_type in {"LOCAL", "WEB", "URL"}:
            unified.append(
                UnifiedEvidence.model_validate(item).model_dump(
                    mode="json", exclude_none=True
                )
            )
        elif promote_local and source_type in {"structured", "unstructured"}:
            unified.append(
                upgrade_local_evidence(item).model_dump(mode="json", exclude_none=True)
            )
        else:
            unified.append(dict(item))
    return unified


def serialize_unified_evidence(
    evidence: dict[str, Any] | UnifiedEvidence,
) -> dict[str, Any]:
    model = (
        evidence
        if isinstance(evidence, UnifiedEvidence)
        else UnifiedEvidence.model_validate(evidence)
    )
    return model.model_dump(mode="json", exclude_none=True)


def deserialize_unified_evidence(
    payload: str | dict[str, Any],
) -> UnifiedEvidence:
    values = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(values, dict):
        raise ValueError("Evidence 4.0 payload 必须是 JSON object")
    return UnifiedEvidence.model_validate(values)


def _explicit_authority(value: Any) -> EvidenceAuthority:
    normalized = str(value or "").strip().casefold()
    if normalized in {"official", "academic", "general", "unknown"}:
        return normalized  # type: ignore[return-value]
    return "unknown"


def _explicit_text(*values: Any) -> str | None:
    return next(
        (str(value).strip() for value in values if str(value or "").strip()),
        None,
    )


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
    if file_name.endswith((".ppt", ".pptx")):
        return "presentation"
    if file_name.endswith(".txt"):
        return "txt"
    if file_name.endswith(".json"):
        return "json"
    if file_name.endswith(".csv"):
        return "csv"
    return None
