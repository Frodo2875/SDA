"""SQLite-backed two-phase confirmation for high-impact file actions."""

from typing import Any
from uuid import uuid4

from backend import database
from backend.runtime.task_runner import resume_after_action
from backend.services.file_lifecycle import delete_uploaded_file
from backend.tools.file_tools import get_file_info
from backend.tools.word_tools import write_word


ACTION_TYPE_WRITE_WORD = "write_word"
ACTION_TYPE_DELETE_FILE = "delete_file"
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

    action = {
        "action_id": uuid4().hex,
        "session_id": session_id,
        "action_type": ACTION_TYPE_WRITE_WORD,
        "target_file": target_file,
        "student_id": str(student_id).strip(),
        "student_name": str(student_name).strip(),
        "content": content.strip(),
        "created_at": database.utc_now(),
        "status": "pending",
        "executed_at": None,
    }
    database.insert_pending_action(action)
    database.save_operation_log(
        session_id=session_id,
        action_type="create_pending_write",
        target_file=target_file,
        student_id=action["student_id"],
        confirmed=False,
        success=True,
    )
    return _success(dict(action), "待确认写入操作已创建，文件尚未修改")


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

    action = {
        "action_id": uuid4().hex,
        "session_id": session_id,
        "action_type": ACTION_TYPE_DELETE_FILE,
        "target_file": record["file_name"],
        "student_id": "",
        "student_name": "",
        "content": clean_file_id,
        "created_at": database.utc_now(),
        "status": "pending",
        "executed_at": None,
    }
    database.insert_pending_action(action)
    database.save_operation_log(
        session_id=session_id,
        action_type="create_pending_delete",
        target_file=record["file_name"],
        student_id="",
        confirmed=False,
        success=True,
    )
    return _success(dict(action), "待确认删除操作已创建，文件尚未删除")


def get_pending_action(action_id: str) -> dict[str, Any]:
    """Read one action without exposing mutable internal state."""
    action = database.get_pending_action_record(action_id)
    if action is None:
        return _failure("ACTION_NOT_FOUND", "未找到指定的待确认操作")
    return _success(action, "操作读取成功")


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
            action,
        )
    database.save_operation_log(
        session_id=action["session_id"],
        action_type=_operation_name(action["action_type"], "cancel"),
        target_file=action["target_file"],
        student_id=action["student_id"],
        confirmed=False,
        success=True,
    )
    _resume_runtime_action(action_id, "cancelled", success=False)
    return _success(action, "操作已取消，文件未修改")


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
            action,
        )
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
    if action_result["ok"]:
        result_key = (
            "write_result"
            if action["action_type"] == ACTION_TYPE_WRITE_WORD
            else "delete_result"
        )
        return _success(
            {"pending_action": action, result_key: action_result},
            "已执行确认的 Word 写入"
            if action["action_type"] == ACTION_TYPE_WRITE_WORD
            else "已执行确认的文件删除",
        )
    return _failure(
        "ACTION_EXECUTION_FAILED",
        action_result["message"],
        {"pending_action": action, "action_result": action_result},
    )


def _execute_frozen_action(action: dict[str, Any]) -> dict[str, Any]:
    if action["action_type"] == ACTION_TYPE_DELETE_FILE:
        try:
            return delete_uploaded_file(action)
        except Exception:
            return _failure("FILE_DELETE_ERROR", "文件删除失败")
    if action["action_type"] != ACTION_TYPE_WRITE_WORD:
        return _failure("UNKNOWN_ACTION_TYPE", "不支持的待确认操作类型")

    try:
        return write_word(action["target_file"], action["content"])
    except Exception:
        return _failure("WORD_WRITE_ERROR", "Word 写入失败")


def _operation_name(action_type: str, verb: str) -> str:
    suffix = "delete" if action_type == ACTION_TYPE_DELETE_FILE else "write"
    return f"{verb}_{suffix}"


def _resume_runtime_action(action_id: str, status: str, *, success: bool) -> None:
    """Keep a completed HITL action authoritative even if Task bookkeeping fails."""
    try:
        resume_after_action(action_id, status, success=success)
    except Exception:
        # Confirmation idempotency and the frozen file action must not be rolled
        # back merely because optional runtime bookkeeping is unavailable.
        return
