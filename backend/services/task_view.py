"""Unified observable Task Center contract for async and Workflow tasks."""

from datetime import datetime, timezone
from typing import Any

from backend import database


STATUS_MAP = {
    "created": "queued",
    "pending": "queued",
    "queued": "queued",
    "running": "running",
    "paused": "paused",
    "waiting_confirmation": "waiting_confirmation",
    "confirmed": "running",
    "success": "success",
    "completed": "success",
    "partial_success": "partial_success",
    "failed": "failed",
    "cancelled": "cancelled",
}
TERMINAL = {"success", "partial_success", "failed", "cancelled"}


def get_task_view(task_id: str) -> dict[str, Any] | None:
    task = database.get_task_record(task_id)
    if task is None:
        return None
    return _view(task)


def list_task_views(session_id: str, limit: int = 100) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    tasks = database.list_task_records_for_session(session_id, 500)
    visible = [
        task for task in tasks
        if not (task.get("checkpoint_data") or {}).get("parent_async_task_id")
    ]
    return [_view(task) for task in visible[:bounded]]


def _view(task: dict[str, Any]) -> dict[str, Any]:
    steps = database.get_task_step_records(task["task_id"])
    raw_status = str(task.get("task_status") or task.get("status") or "pending").casefold()
    status = STATUS_MAP.get(raw_status, raw_status)
    async_meta = (task.get("checkpoint_data") or {}).get("async_task") or {}
    progress_detail = dict(async_meta.get("progress_detail") or {})
    batch = database.get_batch_record_for_task(task["task_id"])
    if batch is not None:
        batch = {
            **batch,
            "failure_details": [
                {
                    "target_id": item.get("target_id"),
                    "error_code": item.get("error_code"),
                    "message": item.get("result_summary"),
                }
                for item in batch.get("items") or []
                if item.get("status") == "failed"
            ],
        }
        processed = sum(
            int(batch.get(key) or 0)
            for key in ("success_count", "failed_count", "skipped_count")
        )
        progress_detail = {
            "unit": "items",
            "processed_items": processed,
            "total_items": int(batch.get("total") or 0),
            "success_count": int(batch.get("success_count") or 0),
            "failed_count": int(batch.get("failed_count") or 0),
            "skipped_count": int(batch.get("skipped_count") or 0),
            "stage": str(batch.get("status") or status),
        }
    elif not progress_detail and steps:
        completed = sum(
            step.get("status") in {"success", "confirmed", "cancelled"}
            for step in steps
        )
        progress_detail = {
            "unit": "nodes",
            "completed_nodes": completed,
            "total_nodes": len(steps),
            "stage": status,
        }
    progress_detail = _with_exact_percent(progress_detail)
    current = next(
        (
            step for step in steps
            if step.get("status") in {"running", "waiting_confirmation"}
        ),
        next(
            (step for step in steps if step.get("status") not in {"success", "cancelled"}),
            None,
        ),
    )
    started_at = next(
        (step.get("started_at") for step in steps if step.get("started_at")), None
    )
    duration_ms = _duration_ms(started_at, task.get("completed_at"))
    failed_step = next((step for step in steps if step.get("status") == "failed"), None)
    error_summary = None
    if task.get("error_code") or failed_step:
        error_summary = {
            "error_code": task.get("error_code"),
            "message": (failed_step or {}).get("failed_reason") or task.get("message"),
            "step_id": (failed_step or {}).get("step_id"),
        }
    elif int(progress_detail.get("failed_count") or 0) > 0:
        error_summary = {
            "error_code": "PARTIAL_FAILURE",
            "message": task.get("message") or "任务部分项目失败",
            "step_id": None,
        }
    enriched = {
        **task,
        "display_status": status,
        "task_summary": task.get("user_message") or task.get("task_type"),
        "current_node": (
            {
                "step_id": current.get("step_id"),
                "name": current.get("step_name"),
                "status": current.get("status"),
            }
            if current else None
        ),
        "progress_detail": progress_detail,
        "document": dict(async_meta.get("document") or {}),
        "started_at": started_at,
        "duration_ms": duration_ms,
        "success_count": int(progress_detail.get("success_count") or 0),
        "failed_count": int(progress_detail.get("failed_count") or 0),
        "skipped_count": int(progress_detail.get("skipped_count") or 0),
        "error_summary": error_summary,
        "cancel_requested": bool(async_meta.get("cancellation_requested")),
        "can_cancel": bool(async_meta)
        and status in {"queued", "running", "paused"}
        and not bool(async_meta.get("cancellation_requested")),
        "can_retry": bool(async_meta) and status in {"failed", "partial_success"},
        "can_resume": bool(async_meta) and status in {"paused", "cancelled", "failed"},
        "terminal": status in TERMINAL,
    }
    return {"task": enriched, "steps": steps, "batch": batch}


def _with_exact_percent(detail: dict[str, Any]) -> dict[str, Any]:
    result = dict(detail)
    pairs = (
        ("processed_pages", "total_pages"),
        ("processed_items", "total_items"),
        ("completed_nodes", "total_nodes"),
    )
    for processed_key, total_key in pairs:
        total = int(result.get(total_key) or 0)
        if total > 0:
            processed = max(0, min(int(result.get(processed_key) or 0), total))
            result["percent"] = int(processed * 100 / total)
            break
    return result


def _duration_ms(started_at: Any, completed_at: Any) -> int | None:
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(str(started_at))
        end = (
            datetime.fromisoformat(str(completed_at))
            if completed_at else datetime.now(timezone.utc)
        )
        return max(0, int((end - start).total_seconds() * 1000))
    except (TypeError, ValueError):
        return None
