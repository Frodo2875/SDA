"""Safety labels and high-impact guards for untrusted visual document data."""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


VISUAL_UNTRUSTED_SOURCES = frozenset({
    "jpg", "jpeg", "png", "pdf_image", "image_pdf", "ocr",
    "ocr_result", "handwriting", "visual_model", "table_ocr", "kie",
    "image", "visual_pdf", "visual_table",
})
VisualReviewDecision = Literal["confirm", "reject", "require_user_review"]


class VisualDataAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trust_level: Literal["untrusted_document_data"] = "untrusted_document_data"
    instruction_authority: Literal["none"] = "none"
    approval_authority: Literal["none"] = "none"
    can_change_system_policy: bool = False
    can_change_tool_risk: bool = False
    can_trigger_tool: bool = False
    can_approve: bool = False
    detected_patterns: list[str] = Field(default_factory=list)
    requires_user_review: bool = False


class VisualWriteAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: VisualReviewDecision
    reason_code: str
    evidence_ids: list[str] = Field(default_factory=list)
    real_approval_still_required: bool = True


PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POLICY_OVERRIDE_TEXT", re.compile(r"忽略(?:之前|以上|所有)?(?:的)?(?:系统)?规则|ignore\s+(?:all\s+)?(?:system\s+)?(?:rules|instructions)", re.I)),
    ("TOOL_NAME_TEXT", re.compile(r"\b(?:delete_file|write_word|rollback_word|undo_word|reindex_document)\b", re.I)),
    ("TOOL_CALL_JSON_TEXT", re.compile(r"[\{\[]\s*[\"']?(?:tool|function|tool_call)[\"']?\s*:", re.I)),
    ("FAKE_CONFIRMED_TEXT", re.compile(r"\bconfirmed\s*=\s*true\b|[\"']confirmed[\"']\s*:\s*true", re.I)),
    ("FAKE_APPROVAL_TEXT", re.compile(r"fake\s*approval|伪造(?:的)?审批|审批已通过|approval\s*[:=]\s*[\"']?(?:approved|true)[\"']?", re.I)),
    ("SCREENSHOT_COMMAND_TEXT", re.compile(r"screenshot\s+(?:command|cmd)|截图命令|点击(?:此)?按钮执行|扫码(?:执行|确认)|QR\s*(?:command|button)", re.I)),
)


def assess_visual_document_data(
    text: str,
    *,
    source_type: str,
    confidence: float | None = None,
    review_required: bool = False,
) -> dict[str, Any]:
    """Label visual content as data even when it resembles instructions."""
    # Unknown adapters are treated at least as strictly as known visual sources.
    _ = str(source_type or "unknown").strip().casefold()
    content = str(text or "")
    detected = [code for code, pattern in PATTERNS if pattern.search(content)]
    low_confidence = (
        isinstance(confidence, (int, float)) and float(confidence) < 0.70
    )
    return VisualDataAssessment(
        detected_patterns=detected,
        requires_user_review=bool(detected or review_required or low_confidence),
    ).model_dump()


def assess_visual_high_impact_write(
    evidence: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Gate visual Evidence before the existing HIGH-risk Approval workflow."""
    visual = [item for item in (evidence or []) if _is_visual_evidence(item)]
    if not visual:
        return VisualWriteAssessment(
            decision="confirm",
            reason_code="NO_UNSAFE_VISUAL_EVIDENCE",
        ).model_dump()
    evidence_ids = [
        str(item["evidence_id"]) for item in visual if item.get("evidence_id")
    ]
    if any(item.get("conflict_sources") or item.get("evidence_status") == "conflict" for item in visual):
        return VisualWriteAssessment(
            decision="reject",
            reason_code="VISUAL_EVIDENCE_CONFLICT",
            evidence_ids=evidence_ids,
        ).model_dump()
    if any(_unsafe_visual_evidence(item) for item in visual):
        return VisualWriteAssessment(
            decision="require_user_review",
            reason_code="LOW_CONFIDENCE_VISUAL_WRITE_REQUIRES_REVIEW",
            evidence_ids=evidence_ids,
        ).model_dump()
    return VisualWriteAssessment(
        decision="confirm",
        reason_code="VISUAL_EVIDENCE_ELIGIBLE_FOR_REAL_APPROVAL",
        evidence_ids=evidence_ids,
    ).model_dump()


def _is_visual_evidence(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    locator = str(item.get("locator_type") or "").casefold()
    recognition = str(item.get("recognition_type") or "").casefold()
    return (
        locator in {"visual_pdf", "image", "visual_table", "handwriting"}
        or recognition in {"printed", "handwritten", "mixed", "unknown"}
        or bool(item.get("region_id"))
    )


def _unsafe_visual_evidence(item: dict[str, Any]) -> bool:
    confidence = item.get("handwriting_confidence", item.get("confidence"))
    low_confidence = (
        confidence is None
        or isinstance(confidence, (int, float)) and float(confidence) < 0.70
    )
    security = assess_visual_document_data(
        str(item.get("text_excerpt") or item.get("value_summary") or ""),
        source_type=str(item.get("locator_type") or item.get("recognition_type") or "visual"),
        confidence=confidence if isinstance(confidence, (int, float)) else None,
        review_required=bool(item.get("review_required")),
    )
    return bool(
        item.get("safe_for_high_impact") is False
        or item.get("review_required")
        or low_confidence
        or security["detected_patterns"]
    )


__all__ = [
    "VISUAL_UNTRUSTED_SOURCES",
    "VisualDataAssessment",
    "VisualWriteAssessment",
    "assess_visual_document_data",
    "assess_visual_high_impact_write",
]
