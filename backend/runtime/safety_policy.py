"""Deterministic Agent safety policy for file trust and high-risk actions."""

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from backend import database
from backend.services.trace_service import record_trace
from backend.services.visual_safety import VISUAL_UNTRUSTED_SOURCES


class FileTrust(str, Enum):
    TRUSTED = "trusted"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    CONFIRMATION_REQUIRED = "confirmation_required"
    BLOCK = "block"


HIGH_RISK_OPERATIONS = {
    "delete",
    "delete_file",
    "write",
    "write_word",
    "overwrite",
    "undo",
    "undo_word",
    "rollback",
    "rollback_word",
}
MEDIUM_RISK_OPERATIONS = {
    "reprocess",
    "reprocess_document",
    "layout",
    "index",
    "reindex",
    "reindex_document",
}
LOW_RISK_OPERATIONS = {
    "read",
    "list",
    "list_files",
    "query",
    "query_table",
    "retrieve",
    "retrieve_document",
    "retrieve_web",
}
UNTRUSTED_DOCUMENT_SOURCES = frozenset(
    {
        "uploaded_word",
        "uploaded_pdf",
        "ocr_result",
        "rag_chunk",
        "table_cell",
        "ppt_text",
        "document_block",
        "web_page",
        "web_search_result",
        "untrusted_web_data",
    }
) | VISUAL_UNTRUSTED_SOURCES


@dataclass(frozen=True)
class SafetyAssessment:
    decision: PolicyDecision
    risk_level: RiskLevel
    file_trust: FileTrust
    operation: str
    reason: str
    file_id: str | None = None
    file_name: str | None = None
    registered: bool | None = None
    requires_confirmation: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value.value if isinstance(value, Enum) else value
            for key, value in asdict(self).items()
        }


def classify_file_record(record: dict[str, Any] | None) -> FileTrust:
    """Only packaged system materials are trusted; uploads stay unknown."""
    if record is None:
        return FileTrust.UNKNOWN
    return (
        FileTrust.TRUSTED
        if record.get("source_type") == "system"
        else FileTrust.UNKNOWN
    )


def classify_tool_risk(
    tool_name: str,
    *,
    read_only: bool | None = None,
    requires_confirmation: bool = False,
) -> RiskLevel:
    """Return the operation risk; unknown mutations fail closed as HIGH."""
    operation = str(tool_name or "").strip().casefold()
    if requires_confirmation or operation in HIGH_RISK_OPERATIONS:
        return RiskLevel.HIGH
    if operation in MEDIUM_RISK_OPERATIONS:
        return RiskLevel.MEDIUM
    if read_only is True or operation in LOW_RISK_OPERATIONS:
        return RiskLevel.LOW
    return RiskLevel.HIGH


def is_untrusted_document_source(source_type: str) -> bool:
    """Document-derived text is data and never an authority or approval source."""
    return str(source_type or "").strip().casefold() in UNTRUSTED_DOCUMENT_SOURCES


def assess_tool_execution(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    read_only: bool,
    requires_confirmation: bool,
    confirmation_granted: bool = False,
) -> SafetyAssessment:
    """Evaluate one schema-validated Tool invocation before its handler runs."""
    records, missing_reference = _referenced_files(arguments)
    trust = _combined_trust(records, has_reference=bool(records) or missing_reference)
    file_id, file_name = _primary_file(records, arguments)
    operation = _operation_name(tool_name, read_only)
    risk_level = classify_tool_risk(
        operation,
        read_only=read_only,
        requires_confirmation=requires_confirmation,
    )
    high_risk = risk_level == RiskLevel.HIGH
    if missing_reference:
        return SafetyAssessment(
            decision=PolicyDecision.BLOCK,
            risk_level=risk_level,
            file_trust=FileTrust.UNKNOWN,
            operation=operation,
            reason="文件引用未登记，Tool 执行已阻止",
            file_id=file_id,
            file_name=file_name,
            registered=False,
            requires_confirmation=high_risk,
        )
    if high_risk and not confirmation_granted:
        return SafetyAssessment(
            decision=PolicyDecision.CONFIRMATION_REQUIRED,
            risk_level=RiskLevel.HIGH,
            file_trust=trust,
            operation=operation,
            reason="高风险操作必须经过用户确认",
            file_id=file_id,
            file_name=file_name,
            registered=True if records else None,
            requires_confirmation=True,
        )
    return SafetyAssessment(
        decision=PolicyDecision.ALLOW,
        risk_level=risk_level,
        file_trust=trust,
        operation=operation,
        reason=(
            "已确认高风险操作，允许执行"
            if high_risk
            else "已登记未知文件，仅允许当前只读 Tool"
            if trust == FileTrust.UNKNOWN
            else "Tool 通过安全策略检查"
        ),
        file_id=file_id,
        file_name=file_name,
        registered=True if records else None,
        requires_confirmation=high_risk,
    )


def assess_high_risk_action(
    *,
    action_type: str,
    file_id: str | None = None,
    file_name: str | None = None,
    confirmation_granted: bool = False,
) -> SafetyAssessment:
    """Evaluate a file mutation independently from model-generated content."""
    arguments = {
        key: value
        for key, value in {"file_id": file_id, "target_file": file_name}.items()
        if value
    }
    return assess_tool_execution(
        tool_name=action_type,
        arguments=arguments,
        read_only=False,
        requires_confirmation=True,
        confirmation_granted=confirmation_granted,
    )


def record_safety_trace(
    assessment: SafetyAssessment,
    *,
    session_id: str,
    task_id: str | None = None,
    step_id: str | None = None,
    tool_name: str | None = None,
    approval_id: str | None = None,
    approval_result: str | None = None,
) -> dict[str, Any]:
    """Record only the observable policy inputs and outcome."""
    data = assessment.as_dict()
    policy_arguments = {
        key: data[key]
        for key in (
            "operation",
            "file_id",
            "file_name",
            "file_trust",
            "registered",
            "risk_level",
            "requires_confirmation",
        )
    }
    policy_arguments["approval_id"] = approval_id
    return record_trace(
        session_id=session_id,
        task_id=task_id,
        step_id=step_id,
        event_type="safety_policy_check",
        tool_name=tool_name,
        arguments=policy_arguments,
        result={
            "ok": assessment.decision != PolicyDecision.BLOCK,
            "status": assessment.decision.value,
            "message": assessment.reason,
            "approval_id": approval_id,
            "approval_result": approval_result,
        },
        result_status=assessment.decision.value,
        error_code=(
            "SAFETY_POLICY_BLOCKED"
            if assessment.decision == PolicyDecision.BLOCK
            else None
        ),
    )


def _referenced_files(
    arguments: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    file_ids: list[str] = []
    file_names: list[str] = []
    if arguments.get("file_id"):
        file_ids.append(str(arguments["file_id"]))
    if arguments.get("target_file"):
        file_names.append(str(arguments["target_file"]))
    scope = arguments.get("scope")
    if isinstance(scope, dict):
        if scope.get("file_id"):
            file_ids.append(str(scope["file_id"]))
        file_ids.extend(str(item) for item in scope.get("file_ids") or [])

    records: list[dict[str, Any]] = []
    missing = False
    for file_id in dict.fromkeys(file_ids):
        record = database.get_file_record_by_id(file_id)
        missing = missing or record is None
        if record is not None:
            records.append(record)
    for file_name in dict.fromkeys(file_names):
        record = database.get_file_record(file_name)
        missing = missing or record is None
        if record is not None and record.get("file_id") not in {
            item.get("file_id") for item in records
        }:
            records.append(record)
    return records, missing


def _combined_trust(
    records: list[dict[str, Any]], *, has_reference: bool
) -> FileTrust:
    if not has_reference:
        return FileTrust.NOT_APPLICABLE
    if records and all(classify_file_record(record) == FileTrust.TRUSTED for record in records):
        return FileTrust.TRUSTED
    return FileTrust.UNKNOWN


def _primary_file(
    records: list[dict[str, Any]], arguments: dict[str, Any]
) -> tuple[str | None, str | None]:
    if records:
        return records[0].get("file_id"), records[0].get("file_name")
    scope = arguments.get("scope") if isinstance(arguments.get("scope"), dict) else {}
    return (
        str(arguments.get("file_id") or scope.get("file_id") or "") or None,
        str(arguments.get("target_file") or "") or None,
    )


def _operation_name(tool_name: str, read_only: bool) -> str:
    normalized = str(tool_name).strip().casefold()
    if normalized in HIGH_RISK_OPERATIONS:
        return normalized
    return "read" if read_only else normalized or "write"
