"""In-memory two-phase confirmation for Word write actions."""

from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from backend.tools.file_tools import get_file_info
from backend.tools.word_tools import write_word


ACTION_TYPE_WRITE_WORD = "write_word"
ACTION_STATUSES = {"pending", "confirmed", "cancelled", "executed", "failed"}

_ACTIONS: dict[str, dict[str, Any]] = {}
_ACTIONS_LOCK = Lock()


def _success(data: Any, message: str) -> dict[str, Any]:
    return {"ok": True, "data": data, "error_code": None, "message": message}


def _failure(error_code: str, message: str, data: Any = None) -> dict[str, Any]:
    return {
        "ok": False,
        "data": data,
        "error_code": error_code,
        "message": message,
    }


def _snapshot(action: dict[str, Any]) -> dict[str, Any]:
    """Return a copy so callers cannot mutate the stored action."""
    return dict(action)


def create_pending_action(
    *,
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
        "action_type": ACTION_TYPE_WRITE_WORD,
        "target_file": target_file,
        "student_id": str(student_id).strip(),
        "student_name": str(student_name).strip(),
        "content": content.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    with _ACTIONS_LOCK:
        _ACTIONS[action["action_id"]] = action
    return _success(_snapshot(action), "待确认写入操作已创建，文件尚未修改")


def get_pending_action(action_id: str) -> dict[str, Any]:
    """Read one action without exposing mutable internal state."""
    with _ACTIONS_LOCK:
        action = _ACTIONS.get(action_id)
        if action is None:
            return _failure("ACTION_NOT_FOUND", "未找到指定的待确认操作")
        return _success(_snapshot(action), "操作读取成功")


def cancel_action(action_id: str) -> dict[str, Any]:
    """Cancel one pending action without touching its target file."""
    with _ACTIONS_LOCK:
        action = _ACTIONS.get(action_id)
        if action is None:
            return _failure("ACTION_NOT_FOUND", "未找到指定的待确认操作")
        if action["status"] != "pending":
            return _failure(
                "ACTION_NOT_PENDING",
                f"操作当前状态为 {action['status']}，不能取消",
                _snapshot(action),
            )
        action["status"] = "cancelled"
        return _success(_snapshot(action), "操作已取消，文件未修改")


def confirm_action(action_id: str) -> dict[str, Any]:
    """Execute exactly the content frozen in one pending action, at most once."""
    with _ACTIONS_LOCK:
        action = _ACTIONS.get(action_id)
        if action is None:
            return _failure("ACTION_NOT_FOUND", "未找到指定的待确认操作")
        if action["status"] != "pending":
            return _failure(
                "ACTION_NOT_PENDING",
                f"操作当前状态为 {action['status']}，不能重复执行",
                _snapshot(action),
            )
        action["status"] = "confirmed"
        target_file = action["target_file"]
        frozen_content = action["content"]

    write_result = write_word(target_file, frozen_content)

    with _ACTIONS_LOCK:
        action = _ACTIONS[action_id]
        if write_result["ok"]:
            action["status"] = "executed"
            return _success(
                {"pending_action": _snapshot(action), "write_result": write_result},
                "已执行确认的 Word 写入",
            )
        action["status"] = "failed"
        return _failure(
            "ACTION_EXECUTION_FAILED",
            write_result["message"],
            {"pending_action": _snapshot(action), "write_result": write_result},
        )
