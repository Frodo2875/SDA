"""Deterministic security labels for untrusted Web text.

Detection is observability, not sanitization: source text is preserved verbatim
and never gains instruction, Tool, policy, or approval authority.
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WebContentAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    untrusted_content: bool = True
    detected_untrusted_patterns: list[str] = Field(default_factory=list)
    instruction_authority: str = "none"
    can_trigger_tool: bool = False
    can_change_tool_risk: bool = False


WEB_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "INSTRUCTION_OVERRIDE",
        re.compile(
            r"ignore\s+(?:all\s+)?(?:previous|prior|above|system)?\s*"
            r"(?:instructions?|rules?)|忽略(?:之前|以上|所有)?(?:的)?(?:系统)?(?:指令|规则)",
            re.I,
        ),
    ),
    (
        "SYSTEM_PROMPT_REFERENCE",
        re.compile(r"\bsystem\s+prompt\b|系统提示(?:词|语)?", re.I),
    ),
    (
        "TOOL_EXECUTION_INSTRUCTION",
        re.compile(
            r"\b(?:execute|run|call|invoke)\s+(?:a\s+|the\s+)?(?:tool|function|command)\b"
            r"|执行(?:这个|以下|任意)?(?:工具|函数|命令)|调用(?:工具|函数)",
            re.I,
        ),
    ),
    (
        "TOOL_PERMISSION_OVERRIDE",
        re.compile(
            r"(?:enable|allow|grant|change|override)\s+(?:all\s+)?tool(?:s|\s+permissions?)?"
            r"|(?:启用|允许|授予|修改|覆盖)(?:所有)?工具(?:权限)?",
            re.I,
        ),
    ),
    (
        "DANGEROUS_TOOL_NAME",
        re.compile(
            r"\b(?:delete_file|write_word|rollback_word|undo_word|reindex_document)\b",
            re.I,
        ),
    ),
)


def assess_web_content(*values: object) -> dict[str, object]:
    """Label instruction-like text without deleting or rewriting the content."""
    content = "\n".join(_bounded_text_values(values))
    patterns = [
        code for code, pattern in WEB_INJECTION_PATTERNS if pattern.search(content)
    ]
    return WebContentAssessment(
        detected_untrusted_patterns=patterns,
    ).model_dump()


def secure_web_evidence(evidence: Any, source_record: Any) -> Any:
    """Attach Web security labels after the stable Evidence Factory boundary."""
    from backend.evidence import UnifiedEvidence

    canonical = (
        evidence
        if isinstance(evidence, UnifiedEvidence)
        else UnifiedEvidence.model_validate(evidence)
    )
    values = (
        source_record.model_dump(mode="json")
        if isinstance(source_record, BaseModel)
        else dict(source_record or {})
    )
    patterns = list(values.get("detected_untrusted_patterns") or [])
    if not patterns:
        security = assess_web_content(
            values.get("title"), values.get("snippet"), values.get("content"),
            values.get("metadata"),
        )
        patterns = list(security["detected_untrusted_patterns"])
    payload = canonical.model_dump(mode="json")
    payload["metadata"] = {
        **dict(canonical.metadata),
        "untrusted_content": True,
    }
    payload["detected_untrusted_patterns"] = patterns
    return UnifiedEvidence.model_validate(payload)


def _bounded_text_values(values: object, limit: int = 100_000) -> list[str]:
    """Flatten bounded external metadata for detection, never for execution."""
    pending = [values]
    collected: list[str] = []
    length = 0
    while pending and length < limit and len(collected) < 1_000:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(reversed(list(value.values())[:1_000]))
            continue
        if isinstance(value, (list, tuple, set)):
            pending.extend(reversed(list(value)[:1_000]))
            continue
        if value is None:
            continue
        text = str(value)[: max(0, limit - length)]
        if text:
            collected.append(text)
            length += len(text)
    return collected


__all__ = [
    "WEB_INJECTION_PATTERNS",
    "WebContentAssessment",
    "assess_web_content",
    "secure_web_evidence",
]
