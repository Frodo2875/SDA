"""SQLite-backed two-phase confirmation for high-impact file actions."""

import json
from typing import Any
from uuid import uuid4

from backend import database
from backend.runtime.planner import PlannedStep, TaskPlan
from backend.runtime.task_runner import (
    begin_confirmed_action,
    finalize_task,
    record_tool_execution,
    resume_after_action,
    start_task,
)
from backend.runtime.context_manager import clear_pending_action, set_pending_action
from backend.runtime.safety_policy import (
    FileTrust,
    PolicyDecision,
    RiskLevel,
    SafetyAssessment,
    assess_high_risk_action,
    record_safety_trace,
)
from backend.services.file_versioning import (
    WordDiffOperation,
    execute_versioned_word_action,
    preview_word_diff,
)
from backend.services.file_lifecycle import delete_uploaded_file
from backend.services.trace_service import record_trace
from backend.services.visual_safety import assess_visual_high_impact_write
from backend.tools.file_tools import get_file_info
from backend.tools.word_tools import write_word


ACTION_TYPE_WRITE_WORD = "write_word"
ACTION_TYPE_WRITE_REPORT = "write_report"
ACTION_TYPE_DELETE_FILE = "delete_file"
ACTION_TYPE_UNDO_WORD = "undo_word"
ACTION_TYPE_ROLLBACK_WORD = "rollback_word"
ACTION_STATUSES = {"pending", "confirmed", "cancelled", "executed", "failed"}


def _success(data: Any, message: str) -> dict[str, Any]:
    return {"ok": True, "data": data, "error_code": None, "message": message}


def _failure(error_code: str, message: str, data: Any = None) -> dict[str, Any]:
    return {
        "ok": False,
        "data": data,
        "error_code": error_code,
        "message": message,
    }


def create_pending_action(
    *,
    session_id: str,
    target_file: str,
    student_id: str,
    student_name: str,
    content: str,
    task_id: str | None = None,
    evidence_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Freeze one proposed Word append without modifying the target file."""
    file_result = get_file_info(target_file)
    if not file_result["ok"]:
        return _failure(
            file_result["error_code"] or "INVALID_TARGET_FILE",
            file_result["message"],
            file_result.get("data"),
        )
    if file_result["data"]["file_type"] != "word":
        return _failure("INVALID_TARGET_FILE", "待确认写入目标必须是 Word 文件")
    if not isinstance(content, str) or not content.strip():
        return _failure("INVALID_ACTION_CONTENT", "待写入内容不能为空")

    visual_guard = assess_visual_high_impact_write(evidence_context)
    try:
        if visual_guard["reason_code"] != "NO_UNSAFE_VISUAL_EVIDENCE":
            record_trace(
                session_id=session_id,
                task_id=task_id,
                event_type="visual_write_guard",
                tool_name="write_word",
                arguments={"evidence_ids": visual_guard["evidence_ids"]},
                result={
                    "ok": visual_guard["decision"] == "confirm",
                    "status": visual_guard["decision"],
                    "message": visual_guard["reason_code"],
                },
                result_status=visual_guard["decision"],
                error_code=(
                    None if visual_guard["decision"] == "confirm"
                    else visual_guard["reason_code"]
                ),
                metrics={
                    "review_decision": visual_guard["decision"],
                    "reason_code": visual_guard["reason_code"],
                    "real_approval_still_required": True,
                },
            )
    except Exception:
        pass
    if visual_guard["decision"] == "reject":
        return _failure(
            "VISUAL_FIELD_REJECTED",
            "视觉字段 Evidence 存在冲突，已拒绝进入高影响写入流程。",
            visual_guard,
        )
    if visual_guard["decision"] == "require_user_review":
        return _failure(
            "VISUAL_FIELD_REVIEW_REQUIRED",
            "低置信度视觉字段必须先由用户核对，不能直接进入写入 Approval。",
            visual_guard,
        )

    record = database.get_file_record(target_file)
    if record is None:
        return _failure("FILE_NOT_FOUND", "未找到目标文件记录")
    preview = preview_word_diff(
        record["file_id"],
        WordDiffOperation(operation_type="append", content=content.strip()),
    )
    if not preview["ok"]:
        return preview
    return _create_pending_word_action(
        session_id=session_id,
        action_type=ACTION_TYPE_WRITE_WORD,
        preview=preview,
        student_id=str(student_id).strip(),
        student_name=str(student_name).strip(),
        content=content.strip(),
        task_id=task_id,
        finalize_standalone=False,
    )


def create_pending_undo_action(
    *, session_id: str, file_id: str
) -> dict[str, Any]:
    """Prepare an undo Diff and reuse the existing pending-action confirmation."""
    preview = preview_word_diff(file_id, WordDiffOperation(operation_type="undo"))
    if not preview["ok"]:
        return preview
    return _create_pending_word_action(
        session_id=session_id,
        action_type=ACTION_TYPE_UNDO_WORD,
        preview=preview,
        student_id="",
        student_name="",
        content="撤销最近一次 Word 修改",
        task_id=None,
        finalize_standalone=True,
    )


def rollback_file(
    file_id: str, version_id: str, *, session_id: str = "direct"
) -> dict[str, Any]:
    """Prepare—not execute—a confirmed rollback to an immutable version."""
    preview = preview_word_diff(
        file_id,
        WordDiffOperation(operation_type="rollback", version_id=version_id),
    )
    if not preview["ok"]:
        return preview
    return _create_pending_word_action(
        session_id=session_id,
        action_type=ACTION_TYPE_ROLLBACK_WORD,
        preview=preview,
        student_id="",
        student_name="",
        content=f"恢复 Word 到版本 {version_id}",
        task_id=None,
        finalize_standalone=True,
    )


def _create_pending_word_action(
    *,
    session_id: str,
    action_type: str,
    preview: dict[str, Any],
    student_id: str,
    student_name: str,
    content: str,
    task_id: str | None,
    finalize_standalone: bool,
) -> dict[str, Any]:
    preview_data = preview["data"]
    action_id = uuid4().hex
    if task_id is None:
        task_type = {
            ACTION_TYPE_WRITE_WORD: "word_write",
            ACTION_TYPE_WRITE_REPORT: "report_write",
            ACTION_TYPE_UNDO_WORD: "word_undo",
            ACTION_TYPE_ROLLBACK_WORD: "word_rollback",
        }[action_type]
        task = start_task(
            session_id=session_id,
            user_message=content,
            plan=TaskPlan(
                task_type=task_type,
                steps=(
                    PlannedStep(1, "生成 Word Diff 预览", "tool", "preview_word_diff"),
                    PlannedStep(2, "等待用户确认", "confirmation", "confirm_action"),
                    PlannedStep(3, "执行确认操作", "side_effect", action_type),
                ),
            ),
        )
        task_id = task["task_id"]
        finalize_standalone = True
    assessment = assess_high_risk_action(
        action_type=action_type,
        file_id=preview_data["file_id"],
        file_name=preview_data["target_file"],
    )
    _record_action_safety(
        assessment,
        session_id=session_id,
        task_id=task_id,
        action_type=action_type,
        approval_id=action_id,
        approval_result="pending",
    )
    if assessment.decision != PolicyDecision.CONFIRMATION_REQUIRED:
        return _failure("SAFETY_POLICY_BLOCKED", assessment.reason)
    if task_id is not None and database.get_task_record(task_id) is None:
        return _failure("TASK_NOT_FOUND", "关联 Task 不存在")
    if task_id is not None:
        record_tool_execution(
            task_id=task_id,
            tool_name="preview_word_diff",
            arguments={
                "file_id": preview_data["file_id"],
                "operation": preview_data["operation"],
            },
            result=preview,
            retry_count=0,
        )

    diff_fields = {
        key: preview_data[key]
        for key in (
            "target_file", "target_object", "location", "operation_type",
            "before", "after", "impact_scope",
        )
    }
    action = {
        "action_id": action_id,
        "session_id": session_id,
        "action_type": action_type,
        "target_file": preview_data["target_file"],
        "student_id": student_id,
        "student_name": student_name,
        "content": content,
        "created_at": database.utc_now(),
        "status": "pending",
        "executed_at": None,
        "file_id": preview_data["file_id"],
        "operation_json": json.dumps(preview_data["operation"], ensure_ascii=False),
        "diff_json": json.dumps(diff_fields, ensure_ascii=False),
        "target_version_id": preview_data["operation"].get("target_version_id"),
        "task_id": task_id,
    }
    database.insert_pending_action(action)
    _set_context_action(session_id, action["action_id"])
    database.save_operation_log(
        session_id=session_id,
        action_type=_operation_name(action_type, "create_pending"),
        target_file=action["target_file"],
        student_id=student_id,
        confirmed=False,
        success=True,
    )
    public_action = _deserialize_action(action)
    if finalize_standalone:
        finalize_task(
            task_id,
            {
                "status": "confirmation_required",
                "pending_action": public_action,
            },
        )
    return _success(public_action, "待确认 Word 操作已创建，文件尚未修改")


def create_pending_delete_action(*, session_id: str, file_id: str) -> dict[str, Any]:
    """Freeze one upload deletion request without touching the file."""
    clean_file_id = str(file_id).strip()
    if not clean_file_id or "/" in clean_file_id or "\\" in clean_file_id:
        return _failure("INVALID_FILE_ID", "file_id 格式无效")
    record = database.get_file_record_by_id(clean_file_id)
    if record is None:
        return _failure("FILE_NOT_FOUND", "未找到指定文件")
    if record["source_type"] != "upload" or not bool(record["deletable"]):
        return _failure("FILE_DELETE_FORBIDDEN", "系统固定文件禁止删除")
    if record["lifecycle_status"] == "deleted":
        return _failure("FILE_ALREADY_DELETED", "文件已经删除")
    action_id = uuid4().hex
    task = start_task(
        session_id=session_id,
        user_message=f"删除文件：{record['file_name']}",
        plan=TaskPlan(
            task_type="file_delete",
            steps=(
                PlannedStep(1, "锁定删除目标", "tool", "preview_delete"),
                PlannedStep(2, "等待用户确认", "confirmation", "confirm_action"),
                PlannedStep(3, "执行确认删除", "side_effect", ACTION_TYPE_DELETE_FILE),
            ),
        ),
    )
    task_id = task["task_id"]
    preview = _success(
        {"file_id": record["file_id"], "file_name": record["file_name"]},
        "删除目标已锁定，实际文件未修改",
    )
    record_tool_execution(
        task_id=task_id,
        tool_name="preview_delete",
        arguments={"file_id": record["file_id"]},
        result=preview,
        retry_count=0,
    )
    assessment = assess_high_risk_action(
        action_type=ACTION_TYPE_DELETE_FILE,
        file_id=record["file_id"],
        file_name=record["file_name"],
    )
    _record_action_safety(
        assessment,
        session_id=session_id,
        task_id=task_id,
        action_type=ACTION_TYPE_DELETE_FILE,
        approval_id=action_id,
        approval_result="pending",
    )
    if assessment.decision != PolicyDecision.CONFIRMATION_REQUIRED:
        return _failure("SAFETY_POLICY_BLOCKED", assessment.reason)

    action = {
        "action_id": action_id,
        "session_id": session_id,
        "action_type": ACTION_TYPE_DELETE_FILE,
        "target_file": record["file_name"],
        "student_id": "",
        "student_name": "",
        "content": clean_file_id,
        "created_at": database.utc_now(),
        "status": "pending",
        "executed_at": None,
        "file_id": record["file_id"],
        "operation_json": None,
        "diff_json": None,
        "target_version_id": None,
        "task_id": task_id,
    }
    database.insert_pending_action(action)
    _set_context_action(session_id, action["action_id"])
    database.save_operation_log(
        session_id=session_id,
        action_type="create_pending_delete",
        target_file=record["file_name"],
        student_id="",
        confirmed=False,
        success=True,
    )
    public_action = _deserialize_action(action)
    finalize_task(
        task_id,
        {"status": "confirmation_required", "pending_action": public_action},
    )
    return _success(public_action, "待确认删除操作已创建，文件尚未删除")


def get_pending_action(action_id: str) -> dict[str, Any]:
    """Read one action without exposing mutable internal state."""
    action = database.get_pending_action_record(action_id)
    if action is None:
        return _failure("ACTION_NOT_FOUND", "未找到指定的待确认操作")
    return _success(_deserialize_action(action), "操作读取成功")


def cancel_action(action_id: str) -> dict[str, Any]:
    """Cancel one pending action without touching its target file."""
    outcome, action = database.cancel_pending_action(action_id)
    if outcome == "not_found":
        return _failure("ACTION_NOT_FOUND", "未找到指定的待确认操作")
    if outcome == "not_pending":
        database.save_operation_log(
            session_id=action["session_id"],
            action_type=_operation_name(action["action_type"], "cancel"),
            target_file=action["target_file"],
            student_id=action["student_id"],
            confirmed=False,
            success=False,
            error_message=f"操作当前状态为 {action['status']}，不能取消",
        )
        return _failure(
            "ACTION_NOT_PENDING",
            f"操作当前状态为 {action['status']}，不能取消",
            _deserialize_action(action),
        )
    database.save_operation_log(
        session_id=action["session_id"],
        action_type=_operation_name(action["action_type"], "cancel"),
        target_file=action["target_file"],
        student_id=action["student_id"],
        confirmed=False,
        success=True,
    )
    cancelled_assessment = assess_high_risk_action(
        action_type=str(action.get("action_type") or ""),
        file_id=_action_file_id(action),
        file_name=str(action.get("target_file") or "") or None,
        confirmation_granted=False,
    )
    _record_action_safety(
        cancelled_assessment,
        session_id=action["session_id"],
        task_id=action.get("task_id"),
        action_type=action["action_type"],
        approval_id=action_id,
        approval_result="cancelled",
    )
    _resume_runtime_action(action_id, "cancelled", success=False)
    _finish_batch_action(action_id, "skipped")
    _synchronize_async_action(action, "cancelled", success=False)
    _clear_context_action(action["session_id"], action_id)
    return _success(_deserialize_action(action), "操作已取消，文件未修改")


def confirm_action(action_id: str) -> dict[str, Any]:
    """Execute exactly the content frozen in one pending action, at most once."""
    outcome, action = database.reserve_pending_action(action_id)
    if outcome == "not_found":
        return _failure("ACTION_NOT_FOUND", "未找到指定的待确认操作")
    if outcome == "not_pending":
        database.save_operation_log(
            session_id=action["session_id"],
            action_type=_operation_name(action["action_type"], "confirm"),
            target_file=action["target_file"],
            student_id=action["student_id"],
            confirmed=True,
            success=False,
            error_message=f"操作当前状态为 {action['status']}，不能重复执行",
        )
        return _failure(
            "ACTION_NOT_PENDING",
            f"操作当前状态为 {action['status']}，不能重复执行",
            _deserialize_action(action),
        )
    begin_confirmed_action(action_id)
    action_result = _execute_frozen_action(action)
    terminal_status = "executed" if action_result["ok"] else "failed"
    action = database.finish_pending_action(action_id, terminal_status)
    database.save_operation_log(
        session_id=action["session_id"],
        action_type=_operation_name(action["action_type"], "confirm"),
        target_file=action["target_file"],
        student_id=action["student_id"],
        confirmed=True,
        success=bool(action_result["ok"]),
        error_message=None if action_result["ok"] else action_result["message"],
    )
    _resume_runtime_action(
        action_id, terminal_status, success=bool(action_result["ok"])
    )
    _finish_batch_action(
        action_id, "success" if action_result["ok"] else "failed"
    )
    _synchronize_async_action(
        action,
        terminal_status,
        success=bool(action_result["ok"]),
        result_message=action_result.get("message"),
    )
    _clear_context_action(action["session_id"], action_id)
    if action_result["ok"]:
        is_delete = action["action_type"] == ACTION_TYPE_DELETE_FILE
        result_key = "delete_result" if is_delete else "write_result"
        return _success(
            {"pending_action": _deserialize_action(action), result_key: action_result},
            "已执行确认的文件删除" if is_delete else "已执行确认的 Word 操作",
        )
    return _failure(
        "ACTION_EXECUTION_FAILED",
        action_result["message"],
        {"pending_action": _deserialize_action(action), "action_result": action_result},
    )


def _execute_frozen_action(action: dict[str, Any]) -> dict[str, Any]:
    binding_error = _approval_binding_error(action)
    if binding_error is not None:
        rejected = SafetyAssessment(
            decision=PolicyDecision.BLOCK,
            risk_level=RiskLevel.HIGH,
            file_trust=FileTrust.UNKNOWN,
            operation=str(action.get("action_type") or "unknown"),
            reason=binding_error,
            file_id=_action_file_id(action),
            file_name=str(action.get("target_file") or "") or None,
            registered=None,
            requires_confirmation=True,
        )
        _record_action_safety(
            rejected,
            session_id=str(action.get("session_id") or "direct"),
            task_id=action.get("task_id"),
            action_type=str(action.get("action_type") or "file_action"),
            approval_id=str(action.get("action_id") or "") or None,
            approval_result="rejected",
        )
        return _failure("APPROVAL_BINDING_MISMATCH", binding_error)
    file_id = _action_file_id(action) or ""
    assessment = assess_high_risk_action(
        action_type=str(action.get("action_type") or ""),
        file_id=file_id or None,
        file_name=str(action.get("target_file") or "") or None,
        confirmation_granted=action.get("status") == "confirmed",
    )
    _record_action_safety(
        assessment,
        session_id=str(action.get("session_id") or "direct"),
        task_id=action.get("task_id"),
        action_type=str(action.get("action_type") or "file_action"),
        approval_id=str(action.get("action_id") or "") or None,
        approval_result="confirmed",
    )
    if assessment.decision != PolicyDecision.ALLOW:
        return _failure("SAFETY_POLICY_BLOCKED", assessment.reason)
    if action["action_type"] == ACTION_TYPE_DELETE_FILE:
        try:
            return delete_uploaded_file(action)
        except Exception:
            return _failure("FILE_DELETE_ERROR", "文件删除失败")
    if action["action_type"] not in {
        ACTION_TYPE_WRITE_WORD,
        ACTION_TYPE_WRITE_REPORT,
        ACTION_TYPE_UNDO_WORD,
        ACTION_TYPE_ROLLBACK_WORD,
    }:
        return _failure("UNKNOWN_ACTION_TYPE", "不支持的待确认操作类型")

    try:
        return execute_versioned_word_action(action, write_handler=write_word)
    except Exception:
        return _failure("WORD_WRITE_ERROR", "Word 写入失败")


def _operation_name(action_type: str, verb: str) -> str:
    suffix = {
        ACTION_TYPE_DELETE_FILE: "delete",
        ACTION_TYPE_UNDO_WORD: "undo",
        ACTION_TYPE_ROLLBACK_WORD: "rollback",
    }.get(action_type, "write")
    return f"{verb}_{suffix}"


def _record_action_safety(
    assessment: SafetyAssessment,
    *,
    session_id: str,
    task_id: str | None,
    action_type: str,
    approval_id: str | None = None,
    approval_result: str | None = None,
) -> None:
    try:
        step_id = None
        if task_id is not None:
            running_steps = [
                step
                for step in database.get_task_step_records(task_id)
                if step.get("status") == "running"
            ]
            if running_steps:
                step_id = running_steps[-1]["step_id"]
        record_safety_trace(
            assessment,
            session_id=session_id,
            task_id=task_id,
            step_id=step_id,
            tool_name=action_type,
            approval_id=approval_id,
            approval_result=approval_result,
        )
    except Exception:
        # Trace is observable but never authoritative over a policy decision.
        pass


def _deserialize_action(action: dict[str, Any]) -> dict[str, Any]:
    item = dict(action)
    item["approval_id"] = item["action_id"]
    item["operation"] = json.loads(item["operation_json"]) if item.get("operation_json") else None
    item["diff_preview"] = json.loads(item["diff_json"]) if item.get("diff_json") else None
    return item


def _action_file_id(action: dict[str, Any]) -> str | None:
    value = action.get("file_id")
    if not value and action.get("action_type") == ACTION_TYPE_DELETE_FILE:
        value = action.get("content")
    return str(value or "").strip() or None


def _approval_binding_error(action: dict[str, Any]) -> str | None:
    """Match a confirmed DB action to its frozen Task, operation and target."""
    approval_id = str(action.get("action_id") or "").strip()
    task_id = str(action.get("task_id") or "").strip()
    if not approval_id or not task_id:
        return "Approval 缺少 approval_id 或 task_id 绑定"
    task = database.get_task_record(task_id)
    if task is None:
        return "Approval 关联 Task 不存在"
    checkpoint = task.get("checkpoint_data") or {}
    binding = checkpoint.get("approval_binding") or {}
    file_id = _action_file_id(action)
    target = {
        "file_id": file_id,
        "file_name": action.get("target_file"),
    }
    frozen_operation = (
        json.loads(action["operation_json"])
        if action.get("operation_json")
        else None
    )
    if (
        checkpoint.get("action_id") != approval_id
        or binding.get("approval_id") != approval_id
        or binding.get("operation") != action.get("action_type")
        or binding.get("target") != target
        or binding.get("frozen_operation") != frozen_operation
    ):
        return "Approval 的 Task、操作或目标绑定不一致"
    record = database.get_file_record_by_id(file_id) if file_id else None
    if record is None or record.get("file_name") != action.get("target_file"):
        return "Approval 目标文件已经变化"
    expected_operation = {
        ACTION_TYPE_WRITE_WORD: "append",
        ACTION_TYPE_WRITE_REPORT: "append",
        ACTION_TYPE_UNDO_WORD: "undo",
        ACTION_TYPE_ROLLBACK_WORD: "rollback",
        ACTION_TYPE_DELETE_FILE: None,
    }.get(action.get("action_type"), "unsupported")
    actual_operation = (
        frozen_operation.get("operation_type")
        if isinstance(frozen_operation, dict)
        else None
    )
    if expected_operation == "unsupported" or actual_operation != expected_operation:
        return "Approval 冻结操作与 Action 类型不一致"
    return None


def _resume_runtime_action(action_id: str, status: str, *, success: bool) -> None:
    """Keep a completed HITL action authoritative even if Task bookkeeping fails."""
    try:
        resume_after_action(action_id, status, success=success)
    except Exception:
        # Confirmation idempotency and the frozen file action must not be rolled
        # back merely because optional runtime bookkeeping is unavailable.
        return


def _finish_batch_action(action_id: str, outcome: str) -> None:
    """Synchronize an optional whole-Batch action without weakening HITL."""
    try:
        database.finish_batch_action(action_id, outcome=outcome)
    except Exception:
        return


def _synchronize_async_action(
    action: dict[str, Any],
    status: str,
    *,
    success: bool,
    result_message: str | None = None,
) -> None:
    """Update an optional outer queue task without making HITL depend on it."""
    try:
        from backend.runtime.async_task_runtime import synchronize_async_action

        synchronize_async_action(
            action_id=action["action_id"],
            session_id=action["session_id"],
            action_status=status,
            success_result=success,
            result_message=result_message,
        )
    except Exception:
        return


def _set_context_action(session_id: str, action_id: str) -> None:
    try:
        set_pending_action(session_id, action_id)
    except Exception:
        return


def _clear_context_action(session_id: str, action_id: str) -> None:
    try:
        clear_pending_action(session_id, action_id)
    except Exception:
        return


def create_pending_report_action(
    *, session_id: str, file_id: str, content: str,
    evidence_context: list[dict[str, Any]],
) -> dict[str, Any]:
    """Report format adapter; reuse the exact frozen approval transaction."""
    guard = assess_visual_high_impact_write(evidence_context)
    if guard["decision"] != "confirm":
        return _failure("VISUAL_FIELD_REVIEW_REQUIRED", "视觉证据必须先经用户核对", guard)
    preview = preview_word_diff(file_id, WordDiffOperation(operation_type="append", content=content))
    if not preview["ok"]:
        return preview
    return _create_pending_word_action(
        session_id=session_id, action_type=ACTION_TYPE_WRITE_REPORT, preview=preview,
        student_id="", student_name="", content=content, task_id=None,
        finalize_standalone=True,
    )
