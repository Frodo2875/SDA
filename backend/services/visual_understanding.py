"""Deterministic V4.4 visual document understanding over persisted OCR regions.

Classification and KIE are derived views. They never replace or mutate source OCR.
"""

import hashlib
import os
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend import database
from backend.document_blocks import BlockType, DocumentBlock
from backend.evidence import build_evidence, make_evidence_id
from backend.services import ocr_service
from backend.services.trace_service import record_trace
from backend.tools.excel_utils import failure, success


VisualBlockType = Literal[
    "title", "paragraph", "table", "image", "signature", "stamp", "unknown"
]
DocumentType = Literal[
    "form", "certificate", "notice", "table", "letter", "report", "unknown"
]


class VisualBlock(DocumentBlock):
    """A V3 DocumentBlock enriched with V4 visual/OCR provenance."""

    image_no: int | None = Field(default=None, ge=1)
    recognition_type: Literal["printed", "handwritten", "mixed", "unknown"]
    source_model: str | None = Field(default=None, min_length=1, max_length=128)
    source_region_ids: list[str] = Field(min_length=1)
    review_required: bool = False
    safe_for_high_impact: bool = True
    safe_for_identity_match: bool = True
    trust_level: Literal["untrusted_document_data"] = "untrusted_document_data"
    instruction_authority: Literal["none"] = "none"
    approval_authority: Literal["none"] = "none"
    can_trigger_tool: bool = False
    can_change_tool_risk: bool = False
    can_approve: bool = False
    detected_untrusted_patterns: list[str] = Field(default_factory=list)

    def visual_dump(self) -> dict[str, Any]:
        payload = self.model_dump()
        payload["document_id"] = self.file_id
        payload["text"] = payload.pop("content")
        return payload


class DocumentClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(min_length=1, max_length=128)
    document_type: DocumentType
    confidence: float = Field(ge=0.0, le=1.0)
    status: Literal["success", "failed"]
    source_parser: str = "visual-rule-classifier"
    evidence_block_ids: list[str] = Field(default_factory=list)
    evidence_region_ids: list[str] = Field(default_factory=list)
    reason: str | None = None
    general_query_allowed: bool = True


class VisualKeyField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str = Field(min_length=1, max_length=128)
    file_id: str = Field(min_length=1, max_length=128)
    domain: Literal["general", "student"]
    field_name: str = Field(min_length=1, max_length=128)
    field_label: str = Field(min_length=1, max_length=128)
    value: str
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: Literal["extracted", "review_required"]
    is_confirmed_fact: bool = False
    safe_for_high_impact: bool = False
    block_id: str = Field(min_length=1, max_length=128)
    source_region_id: str = Field(min_length=1, max_length=128)
    source_parser: str | None = None
    source_model: str | None = None
    evidence: dict[str, Any]
    trust_level: Literal["untrusted_document_data"] = "untrusted_document_data"
    instruction_authority: Literal["none"] = "none"
    approval_authority: Literal["none"] = "none"
    can_trigger_tool: bool = False
    can_change_tool_risk: bool = False
    can_approve: bool = False
    detected_untrusted_patterns: list[str] = Field(default_factory=list)


STUDENT_SCHEMA_HINTS = {
    "姓名": "name",
    "名字": "name",
    "学号": "student_id",
    "学生编号": "student_id",
    "成绩": "score",
    "分数": "score",
    "电话": "phone",
    "手机号": "phone",
}
FIELD_PATTERN = re.compile(r"^\s*([^:：\n]{1,30})\s*[:：]\s*(\S.*?)\s*$")
CANONICAL_FIELD_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def extract_visual_blocks(
    file_id: str,
    *,
    regions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Derive unified visual blocks from the active OCR region ledger."""
    record = database.get_file_record_by_id(str(file_id).strip())
    if record is None:
        return failure("FILE_NOT_FOUND", "未找到指定文件")
    source_regions = list(
        database.get_document_ocr_regions(record["file_id"])
        if regions is None else regions
    )
    if not source_regions:
        return success(
            {"file_id": record["file_id"], "status": "empty", "blocks": []},
            "当前文档没有可派生的视觉 region",
        )
    normalized: list[dict[str, Any]] = []
    for index, region in enumerate(source_regions, start=1):
        try:
            normalized.append(ocr_service.normalize_region_record(
                file_id=record["file_id"],
                page_no=int(region.get("page_no") or region.get("image_no") or 1),
                region_index=index,
                region=region,
                image_input=record["file_type"] == "image",
            ))
        except (TypeError, ValueError):
            continue
    blocks: list[VisualBlock] = []
    page_parent: dict[int, str] = {}
    for sequence, region in enumerate(normalized):
        page_no = int(region.get("page_no") or 1)
        block_type = _infer_block_type(region, sequence, page_no, blocks)
        block_id = _visual_block_id(record["file_id"], region["region_id"], block_type)
        parent_id = None if block_type == "title" else page_parent.get(page_no)
        if block_type == "title":
            page_parent[page_no] = block_id
        degraded = region.get("status") != "success" or bool(region.get("review_required"))
        warnings = []
        if region.get("review_required"):
            warnings.append(str(region.get("review_reason") or "VISUAL_REVIEW_REQUIRED"))
        if region.get("status") != "success":
            warnings.append(f"OCR_REGION_{str(region.get('status')).upper()}")
        blocks.append(VisualBlock(
            block_id=block_id,
            file_id=record["file_id"],
            page_no=page_no,
            image_no=region.get("image_no"),
            block_type=block_type,
            content=str(region.get("text") or ""),
            bbox=region.get("bbox"),
            confidence=region.get("confidence"),
            parent_id=parent_id,
            source_parser=str(region.get("source_parser") or "visual-ocr"),
            source_model=region.get("source_model"),
            recognition_type=str(region.get("recognition_type") or "unknown"),
            source_region_ids=[str(region["region_id"])],
            review_required=bool(region.get("review_required")),
            safe_for_high_impact=bool(region.get("safe_for_high_impact", not degraded)),
            safe_for_identity_match=bool(region.get("safe_for_identity_match", not degraded)),
            trust_level="untrusted_document_data",
            instruction_authority="none",
            approval_authority="none",
            can_trigger_tool=False,
            can_change_tool_risk=False,
            can_approve=False,
            detected_untrusted_patterns=list(
                region.get("detected_untrusted_patterns") or []
            ),
            status="degraded" if degraded else "normal",
            warnings=warnings,
        ))
    status = "partial_success" if any(block.status == "degraded" for block in blocks) else "success"
    return success(
        {
            "file_id": record["file_id"],
            "document_id": record["file_id"],
            "status": status,
            "blocks": [block.visual_dump() for block in blocks],
        },
        "视觉 Block 提取完成",
    )


def classify_document(
    file_id: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Conservatively classify visual documents; unknown remains queryable."""
    started = time.perf_counter()
    record = database.get_file_record_by_id(str(file_id).strip())
    if record is None:
        return failure("FILE_NOT_FOUND", "未找到指定文件")
    try:
        visual_blocks = _load_visual_blocks(record["file_id"], blocks)
        classification = _classify_from_blocks(record["file_id"], visual_blocks)
    except Exception:
        classification = DocumentClassification(
            file_id=record["file_id"], document_type="unknown", confidence=0.0,
            status="failed", reason="VISUAL_CLASSIFICATION_FAILED",
            general_query_allowed=True,
        )
    result = success(classification.model_dump(), "文档分类完成")
    _record_visual_understanding_trace(
        record["file_id"],
        "classification",
        started,
        classification.status,
        {
            "classification_status": classification.status,
            "document_type": classification.document_type,
            "classification_confidence": classification.confidence,
            "region_count": len(classification.evidence_region_ids),
        },
    )
    return result


def extract_key_fields(
    file_id: str,
    *,
    domain: Literal["general", "student"] = "general",
    schema_hint: dict[str, str] | None = None,
    blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Extract explicit label/value candidates with mandatory source-region evidence."""
    started = time.perf_counter()
    record = database.get_file_record_by_id(str(file_id).strip())
    if record is None:
        return failure("FILE_NOT_FOUND", "未找到指定文件")
    try:
        hints = _validated_schema_hints(domain, schema_hint)
        visual_blocks = _load_visual_blocks(record["file_id"], blocks)
        threshold = _kie_confidence_threshold()
    except (ValidationError, ValueError) as exc:
        return failure("VISUAL_KIE_ARGUMENT_INVALID", str(exc))
    fields: list[VisualKeyField] = []
    for block in visual_blocks:
        if block.block_type not in {"paragraph", "table", "unknown"}:
            continue
        for line in block.content.splitlines() or [block.content]:
            match = FIELD_PATTERN.fullmatch(line)
            if match is None:
                continue
            label, value = (part.strip() for part in match.groups())
            if not value:
                continue
            # KIE is only a derived candidate when its source geometry remains
            # openable. OCR text without geometry stays queryable but is not KIE.
            if block.bbox is None:
                continue
            field_name = hints.get(label, label) if domain == "student" else label
            confidence = block.confidence
            needs_review = (
                block.review_required
                or confidence is None
                or confidence < threshold
                or not block.safe_for_high_impact
            )
            region_id = block.source_region_ids[0]
            evidence = build_evidence(
                evidence_id=make_evidence_id(
                    file_id=record["file_id"], block_id=block.block_id,
                    region_id=region_id, field=field_name, value=value,
                ),
                source_type="unstructured",
                locator_type="image" if record["file_type"] == "image" else "visual_pdf",
                file_id=record["file_id"],
                file_name=record["file_name"],
                page_no=block.page_no,
                image_no=block.image_no,
                chunk_id=None,
                block_id=block.block_id,
                region_id=region_id,
                table=None,
                cell=None,
                bbox=block.bbox,
                confidence=confidence,
                recognition_type=block.recognition_type,
                source_parser=block.source_parser,
                source_model=block.source_model,
                field=field_name,
                record_key=None,
                value_summary=value,
                text_excerpt=line.strip(),
                review_required=needs_review,
                trust_level="untrusted_document_data",
                instruction_authority="none",
                approval_authority="none",
                can_trigger_tool=False,
                can_change_tool_risk=False,
                can_approve=False,
                detected_untrusted_patterns=list(block.detected_untrusted_patterns),
            )
            field_id = hashlib.sha256(
                f"{evidence['evidence_id']}:{field_name}".encode("utf-8")
            ).hexdigest()[:32]
            fields.append(VisualKeyField(
                field_id=field_id,
                file_id=record["file_id"],
                domain=domain,
                field_name=field_name,
                field_label=label,
                value=value,
                bbox=block.bbox,
                confidence=confidence,
                status="review_required" if needs_review else "extracted",
                is_confirmed_fact=False,
                safe_for_high_impact=(block.safe_for_high_impact and not needs_review),
                block_id=block.block_id,
                source_region_id=region_id,
                source_parser=block.source_parser,
                source_model=block.source_model,
                evidence=evidence,
                trust_level="untrusted_document_data",
                instruction_authority="none",
                approval_authority="none",
                can_trigger_tool=False,
                can_change_tool_risk=False,
                can_approve=False,
                detected_untrusted_patterns=list(block.detected_untrusted_patterns),
            ))
    result = success(
        {
            "file_id": record["file_id"],
            "domain": domain,
            "schema": "student_field_value" if domain == "student" else "general_field_value",
            "status": (
                "partial_success" if any(field.status == "review_required" for field in fields)
                else "success"
            ),
            "fields": [field.model_dump() for field in fields],
            "source_ocr_preserved": True,
        },
        "视觉关键字段候选提取完成",
    )
    _record_visual_understanding_trace(
        record["file_id"],
        "kie",
        started,
        result["data"]["status"],
        {
            "domain": domain,
            "kie_status": result["data"]["status"],
            "kie_field_count": len(fields),
            "kie_review_required_count": sum(
                field.status == "review_required" for field in fields
            ),
            "region_count": len({field.source_region_id for field in fields}),
        },
    )
    return result


def _record_visual_understanding_trace(
    file_id: str,
    stage: str,
    started: float,
    status: str,
    metrics: dict[str, Any],
) -> None:
    try:
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        record_trace(
            session_id=f"document:{file_id}",
            event_type="visual_understanding",
            tool_name=f"{stage}_document",
            arguments={"file_id": file_id, "stage": stage},
            result={"ok": status != "failed", "status": status, "message": stage},
            duration_ms=duration_ms,
            result_status=status,
            metrics={
                "stage": stage,
                "latency_ms": duration_ms,
                "visual_processing_duration_ms": duration_ms,
                "vision_call_count": 1,
                **metrics,
            },
        )
    except Exception:
        pass


def _load_visual_blocks(
    file_id: str, blocks: list[dict[str, Any]] | None
) -> list[VisualBlock]:
    raw_blocks = blocks
    if raw_blocks is None:
        extracted = extract_visual_blocks(file_id)
        if not extracted["ok"]:
            raise ValueError(extracted["message"])
        raw_blocks = extracted["data"]["blocks"]
    loaded: list[VisualBlock] = []
    for raw in raw_blocks or []:
        payload = dict(raw)
        payload["content"] = payload.pop("text", payload.get("content", ""))
        payload.pop("document_id", None)
        loaded.append(VisualBlock.model_validate(payload))
    return loaded


def _infer_block_type(
    region: dict[str, Any], sequence: int, page_no: int, blocks: list[VisualBlock]
) -> VisualBlockType:
    hinted = region.get("visual_block_type")
    if hinted in {"title", "paragraph", "table", "image", "signature", "stamp", "unknown"}:
        return hinted
    text = str(region.get("text") or "").strip()
    if re.search(r"(?:签名|签字|署名)\s*[:：]?", text):
        return "signature"
    if re.search(r"(?:盖章|公章|印章)", text):
        return "stamp"
    if "\t" in text or (text.count("|") >= 2):
        return "table"
    first_on_page = not any(block.page_no == page_no for block in blocks)
    if first_on_page and text and len(text) <= 40 and (
        re.search(r"(?:证书|证明|通知|公告|申请表|登记表|报告|总结|函)$", text)
        or not re.search(r"[。！？；;]", text)
    ):
        return "title"
    if text:
        return "paragraph"
    return "unknown"


def _classify_from_blocks(
    file_id: str, blocks: list[VisualBlock]
) -> DocumentClassification:
    text = "\n".join(block.content for block in blocks if block.content)
    rules: tuple[tuple[DocumentType, str, float], ...] = (
        ("certificate", r"(?:证书|证明书|获奖证明|荣誉证明)", 0.92),
        ("notice", r"(?:通知|公告|告知书)", 0.90),
        ("form", r"(?:申请表|登记表|信息表|表单)", 0.90),
        ("report", r"(?:报告|总结|分析报告)", 0.88),
        ("letter", r"(?:尊敬的|此致\s*敬礼|来信|函)", 0.86),
    )
    document_type: DocumentType = "unknown"
    confidence = 0.0
    evidence = []
    for candidate, pattern, score in rules:
        matched = [block for block in blocks if re.search(pattern, block.content)]
        if matched:
            document_type, confidence, evidence = candidate, score, matched
            break
    if document_type == "unknown" and blocks and all(
        block.block_type == "table" for block in blocks
    ):
        document_type, confidence, evidence = "table", 0.85, blocks
    return DocumentClassification(
        file_id=file_id,
        document_type=document_type,
        confidence=confidence,
        status="success" if document_type != "unknown" else "failed",
        evidence_block_ids=[block.block_id for block in evidence],
        evidence_region_ids=[
            region_id for block in evidence for region_id in block.source_region_ids
        ],
        reason=None if document_type != "unknown" else "NO_SUPPORTED_DOCUMENT_PATTERN",
        general_query_allowed=True,
    )


def _validated_schema_hints(
    domain: str, schema_hint: dict[str, str] | None
) -> dict[str, str]:
    if domain not in {"general", "student"}:
        raise ValueError("domain 只支持 general 或 student")
    if domain == "general":
        if schema_hint:
            raise ValueError("General 领域不接受学生字段 schema hint")
        return {}
    hints = dict(STUDENT_SCHEMA_HINTS)
    if schema_hint:
        if len(schema_hint) > 50:
            raise ValueError("schema_hint 字段过多")
        for label, canonical in schema_hint.items():
            clean_label = str(label).strip()
            clean_canonical = str(canonical).strip()
            if not clean_label or len(clean_label) > 64 or not CANONICAL_FIELD_PATTERN.fullmatch(clean_canonical):
                raise ValueError("schema_hint 必须是有效 label 到 canonical field 映射")
            hints[clean_label] = clean_canonical
    return hints


def _kie_confidence_threshold() -> float:
    try:
        threshold = float(os.getenv("VISUAL_KIE_CONFIDENCE_THRESHOLD", "0.85"))
    except ValueError as exc:
        raise ValueError("VISUAL_KIE_CONFIDENCE_THRESHOLD 配置无效") from exc
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("VISUAL_KIE_CONFIDENCE_THRESHOLD 必须在 0 到 1 之间")
    return threshold


def _visual_block_id(file_id: str, region_id: str, block_type: BlockType) -> str:
    return hashlib.sha256(
        f"{file_id}:visual:{region_id}:{block_type}".encode("utf-8")
    ).hexdigest()[:32]
