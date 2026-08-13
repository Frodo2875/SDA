"""SQLite-backed two-phase confirmation for Word write actions."""

from typing import Any
from uuid import uuid4

from backend import database
from backend.tools.file_tools import get_file_info
from backend.tools.word_tools import write_word


ACTION_TYPE_WRITE_WORD = "write_word"
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
            action_type="cancel_write",
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
        action_type="cancel_write",
        target_file=action["target_file"],
        student_id=action["student_id"],
        confirmed=False,
        success=True,
    )
    return _success(action, "操作已取消，文件未修改")


def confirm_action(action_id: str) -> dict[str, Any]:
    """Execute exactly the content frozen in one pending action, at most once."""
    outcome, action = database.reserve_pending_action(action_id)
    if outcome == "not_found":
        return _failure("ACTION_NOT_FOUND", "未找到指定的待确认操作")
    if outcome == "not_pending":
        database.save_operation_log(
            session_id=action["session_id"],
            action_type="confirm_write",
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
    target_file = action["target_file"]
    frozen_content = action["content"]

    try:
        write_result = write_word(target_file, frozen_content)
    except Exception:
        write_result = {
            "ok": False,
            "data": None,
            "error_code": "WORD_WRITE_ERROR",
            "message": "Word 写入失败",
        }
    terminal_status = "executed" if write_result["ok"] else "failed"
    action = database.finish_pending_action(action_id, terminal_status)
    database.save_operation_log(
        session_id=action["session_id"],
        action_type="confirm_write",
        target_file=action["target_file"],
        student_id=action["student_id"],
        confirmed=True,
        success=bool(write_result["ok"]),
        error_message=None if write_result["ok"] else write_result["message"],
    )
    if write_result["ok"]:
        return _success(
            {"pending_action": action, "write_result": write_result},
            "已执行确认的 Word 写入",
        )
    return _failure(
        "ACTION_EXECUTION_FAILED",
        write_result["message"],
        {"pending_action": action, "write_result": write_result},
    )
