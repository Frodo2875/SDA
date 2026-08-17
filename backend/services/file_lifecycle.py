"""Lifecycle transitions and confirmed cleanup for registered files."""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend import database
from backend.services.file_locator import FileLocatorError, resolve_by_file_id
from backend.tools.excel_utils import failure, success


Parser = Callable[[Path, str], dict[str, Any] | None]


def process_uploaded_file(
    file_id: str,
    path: Path,
    parser: Parser,
) -> dict[str, Any]:
    """Synchronously parse one registered upload using bounded state transitions."""
    moved_to_processing = database.update_file_state(
        file_id=file_id,
        lifecycle_status="processing",
        parse_status="processing",
        queryable=False,
        expected_lifecycle="uploaded",
    )
    if not moved_to_processing:
        return failure("INVALID_FILE_STATE", "文件不处于可开始解析的 uploaded 状态")

    try:
        parse_error = parser(path, path.suffix.lower())
    except Exception:
        parse_error = failure("FILE_PARSE_ERROR", "文件解析过程中发生错误")

    if parse_error is not None:
        database.update_file_state(
            file_id=file_id,
            lifecycle_status="failed",
            parse_status="failed",
            queryable=False,
            expected_lifecycle="processing",
        )
        return failure(
            parse_error.get("error_code") or "FILE_PARSE_ERROR",
            parse_error.get("message") or "文件解析失败",
            {"file_id": file_id},
        )

    moved_to_ready = database.update_file_state(
        file_id=file_id,
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
        expected_lifecycle="processing",
    )
    if not moved_to_ready:
        return failure("INVALID_FILE_STATE", "文件解析完成，但状态更新失败")
    return success({"file_id": file_id}, "文件解析完成并已就绪")


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
