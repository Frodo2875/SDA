"""Lifecycle transitions and confirmed cleanup for registered files."""

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend import database
from backend.repositories.file_repository import (
    FileLifecycleStatus,
    canonical_lifecycle_status,
)
from backend.services.file_locator import FileLocatorError, resolve_by_file_id
from backend.services.trace_service import record_trace
from backend.tools.excel_utils import failure, success


Parser = Callable[[Path, str], dict[str, Any] | None]
TRACE_SESSION_PREFIX = "file-lifecycle:"


def transition_file_lifecycle(
    file_id: str,
    target_status: FileLifecycleStatus | str,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    resume_status: FileLifecycleStatus | None = None,
) -> dict[str, Any]:
    """Apply and trace one validated V3.3 lifecycle transition."""
    try:
        target = (
            target_status
            if isinstance(target_status, FileLifecycleStatus)
            else FileLifecycleStatus(str(target_status).strip().upper())
        )
    except ValueError:
        return failure("INVALID_LIFECYCLE_STATUS", "文件生命周期状态无效")
    record = database.get_file_record_by_id(str(file_id).strip())
    if record is None:
        return failure("FILE_NOT_FOUND", "未找到指定文件")
    if target == FileLifecycleStatus.FAILED and not error_code:
        return failure("LIFECYCLE_ERROR_REQUIRED", "进入 FAILED 状态必须保存错误信息")
    try:
        transition = database.transition_file_lifecycle(
            file_id=record["file_id"],
            target_status=target,
            resume_status=resume_status,
        )
    except ValueError as exc:
        return failure("INVALID_FILE_STATE", str(exc))
    except RuntimeError as exc:
        return failure("FILE_STATE_CONFLICT", str(exc))
    if transition is None:
        return failure("FILE_NOT_FOUND", "未找到指定文件")
    if transition["changed"]:
        _trace_transition(
            file_id=record["file_id"],
            from_status=transition["from_status"],
            to_status=transition["to_status"],
            error_code=error_code,
            error_message=error_message,
        )
    current = database.get_file_record_by_id(record["file_id"])
    return success(
        {
            "file_id": record["file_id"],
            "status": target.value,
            "changed": bool(transition["changed"]),
            "lifecycle_status": current["lifecycle_status"],
            "parse_status": current["parse_status"],
            "index_status": current["index_status"],
            "queryable": bool(current["queryable"]),
        },
        "文件生命周期状态已更新" if transition["changed"] else "文件已处于目标状态",
    )


def get_file_lifecycle(file_id: str) -> dict[str, Any]:
    """Return canonical state plus the latest persisted failure summary."""
    record = database.get_file_record_by_id(str(file_id).strip())
    if record is None:
        return failure("FILE_NOT_FOUND", "未找到指定文件")
    status = canonical_lifecycle_status(record)
    last_failure = _latest_failure(record["file_id"]) if status == FileLifecycleStatus.FAILED else None
    return success(
        {
            "file_id": record["file_id"],
            "status": status.value,
            "error": last_failure,
            "lifecycle_status": record["lifecycle_status"],
            "parse_status": record["parse_status"],
            "index_status": record["index_status"],
            "queryable": bool(record["queryable"]),
        },
        "文件生命周期读取成功",
    )


def resume_file_lifecycle(file_id: str) -> dict[str, Any]:
    """Resume a failed file from its last durable lifecycle checkpoint."""
    record = database.get_file_record_by_id(str(file_id).strip())
    if record is None:
        return failure("FILE_NOT_FOUND", "未找到指定文件")
    if canonical_lifecycle_status(record) != FileLifecycleStatus.FAILED:
        return failure("INVALID_FILE_STATE", "只有 FAILED 文件可以恢复")
    checkpoint = _latest_failure(record["file_id"])
    if checkpoint is None or not checkpoint.get("resume_status"):
        return failure("NO_LIFECYCLE_CHECKPOINT", "未找到可恢复的生命周期检查点")
    try:
        resume_status = FileLifecycleStatus(checkpoint["resume_status"])
    except ValueError:
        return failure("NO_LIFECYCLE_CHECKPOINT", "生命周期检查点无效")
    return transition_file_lifecycle(
        record["file_id"],
        resume_status,
        resume_status=resume_status,
    )


def start_ocr_processing(file_id: str) -> dict[str, Any]:
    """Enter the OCR phase through the shared restricted state machine."""
    return transition_file_lifecycle(file_id, FileLifecycleStatus.OCR_PROCESSING)


def process_uploaded_file(
    file_id: str,
    path: Path,
    parser: Parser,
) -> dict[str, Any]:
    """Synchronously parse one upload and resume only from a durable checkpoint."""
    lifecycle = get_file_lifecycle(file_id)
    if not lifecycle["ok"]:
        return lifecycle
    current = FileLifecycleStatus(lifecycle["data"]["status"])
    if current == FileLifecycleStatus.FAILED:
        resumed = resume_file_lifecycle(file_id)
        if not resumed["ok"]:
            return resumed
        current = FileLifecycleStatus(resumed["data"]["status"])
    if current == FileLifecycleStatus.UPLOADED:
        detected = transition_file_lifecycle(file_id, FileLifecycleStatus.DETECTING)
        if not detected["ok"]:
            return detected
        current = FileLifecycleStatus.DETECTING
    if current == FileLifecycleStatus.DETECTING:
        parsing = transition_file_lifecycle(file_id, FileLifecycleStatus.PARSING)
        if not parsing["ok"]:
            return parsing
        current = FileLifecycleStatus.PARSING
    if current != FileLifecycleStatus.PARSING:
        return failure("INVALID_FILE_STATE", "文件不处于可开始或恢复解析的状态")

    try:
        parse_error = parser(path, path.suffix.lower())
    except Exception:
        parse_error = failure("FILE_PARSE_ERROR", "文件解析过程中发生错误")

    if parse_error is not None:
        transition_file_lifecycle(
            file_id,
            FileLifecycleStatus.FAILED,
            error_code=parse_error.get("error_code") or "FILE_PARSE_ERROR",
            error_message=parse_error.get("message") or "文件解析失败",
        )
        return failure(
            parse_error.get("error_code") or "FILE_PARSE_ERROR",
            parse_error.get("message") or "文件解析失败",
            {"file_id": file_id},
        )

    moved_to_ready = transition_file_lifecycle(file_id, FileLifecycleStatus.QUERYABLE)
    if not moved_to_ready["ok"]:
        return failure("INVALID_FILE_STATE", "文件解析完成，但状态更新失败")
    return success({"file_id": file_id}, "文件解析完成并已就绪")


def _trace_transition(
    *,
    file_id: str,
    from_status: str,
    to_status: str,
    error_code: str | None,
    error_message: str | None,
) -> None:
    record_trace(
        session_id=f"{TRACE_SESSION_PREFIX}{file_id}",
        event_type="file_lifecycle_transition",
        tool_name="file_lifecycle",
        arguments={
            "file_id": file_id,
            "from_status": from_status,
            "to_status": to_status,
            "resume_status": from_status if to_status == FileLifecycleStatus.FAILED.value else None,
        },
        result={
            "ok": to_status != FileLifecycleStatus.FAILED.value,
            "status": to_status,
            "message": error_message or "文件生命周期状态已更新",
            "error_code": error_code,
        },
        result_status="failed" if to_status == FileLifecycleStatus.FAILED.value else "success",
        error_code=error_code,
    )


def _latest_failure(file_id: str) -> dict[str, Any] | None:
    traces = database.get_session_trace_records(f"{TRACE_SESSION_PREFIX}{file_id}", 100)
    for trace in reversed(traces):
        if (
            trace.get("event_type") != "file_lifecycle_transition"
            or trace.get("result_status") != "failed"
        ):
            continue
        try:
            arguments = json.loads(trace.get("arguments_summary") or "{}")
            result = json.loads(trace.get("result_summary") or "{}")
        except (TypeError, ValueError):
            continue
        return {
            "error_code": trace.get("error_code"),
            "message": result.get("message"),
            "resume_status": arguments.get("resume_status"),
            "created_at": trace.get("created_at"),
        }
    return None


def delete_uploaded_file(action: dict[str, Any]) -> dict[str, Any]:
    """Delete exactly the upload frozen in a confirmed pending action."""
    file_id = str(action.get("content") or "").strip()
    record = database.get_file_record_by_id(file_id)
    if record is None:
        return failure("FILE_NOT_FOUND", "待删除文件记录不存在")
    if record["source_type"] != "upload" or not bool(record["deletable"]):
        return failure("FILE_DELETE_FORBIDDEN", "系统固定文件禁止删除")
    if record["file_name"] != action.get("target_file"):
        return failure("DELETE_TARGET_CHANGED", "待删除文件与确认内容不一致")

    try:
        target = resolve_by_file_id(file_id)
    except FileLocatorError as exc:
        _mark_cleanup_failed(record)
        return failure("FILE_DELETE_ERROR", f"删除文件失败：{exc}")

    staging = target.parent / f".delete-{action['action_id']}-{target.name}"
    original_chunks = database.get_document_chunks(file_id)
    try:
        os.replace(target, staging)
    except OSError:
        _mark_cleanup_failed(record)
        return failure("FILE_DELETE_ERROR", "物理文件无法进入安全删除流程")

    try:
        state_updated = database.update_file_state(
            file_id=file_id,
            lifecycle_status="deleted",
            parse_status=record["parse_status"],
            queryable=False,
            index_status="not_required",
        )
        if not state_updated:
            raise RuntimeError("文件状态更新失败")
        database.delete_document_chunks(file_id)
        staging.unlink()
    except Exception:
        if staging.exists():
            try:
                os.replace(staging, target)
            except OSError:
                pass
        try:
            database.replace_document_chunks(file_id, original_chunks)
        except Exception:
            pass
        _mark_cleanup_failed(record)
        return failure(
            "FILE_DELETE_ERROR",
            "物理文件删除失败，文件已保留并标记为 cleanup_failed",
            {"file_id": file_id, "lifecycle_status": "cleanup_failed"},
        )

    return success(
        {
            "file_id": file_id,
            "file_name": record["file_name"],
            "lifecycle_status": "deleted",
            "exists": False,
        },
        "上传文件已删除",
    )


def _mark_cleanup_failed(record: dict[str, Any]) -> None:
    database.update_file_state(
        file_id=record["file_id"],
        lifecycle_status="cleanup_failed",
        parse_status=record["parse_status"],
        queryable=False,
        index_status=record["index_status"],
    )
