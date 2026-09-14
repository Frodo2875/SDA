"""User-facing research tasks backed exclusively by the existing Async Runtime."""

import asyncio
from typing import Any

from backend import database
from backend.runtime.async_task_runtime import AsyncTaskContext, enqueue_async_task, run_async_task
from backend.schemas import ResearchTaskRequest
from backend.services.redaction import redact_value
from backend.services.trace_service import record_trace


_SUCCESS_STATUSES = {"completed", "clarification_required", "confirmation_required"}
_ERROR_CODES = {
    "max_tool_rounds_exceeded": "RESEARCH_BUDGET_EXHAUSTED",
    "incomplete_tool_calls": "RESEARCH_INCOMPLETE",
}


class ResearchObserver:
    """Persist Agent checkpoints and real stages; does not execute any tools."""

    def __init__(self, context: AsyncTaskContext) -> None:
        self.context = context

    @property
    def task_id(self) -> str:
        return self.context.task_id

    def load(self) -> dict[str, Any]:
        return dict(self.context.checkpoint.get("agent") or {})

    def save(self, **values: Any) -> None:
        # Save completed work even if cancellation arrived during that tool.
        database.TASK_REPOSITORY.update_research(self.context.task_id, agent=values)
        child_id = (values.get("execution") or {}).get("task_id")
        if child_id:
            from backend.runtime.async_task_runtime import _link_child_task

            _link_child_task(self.context.task_id, child_id)

    def stage(self, name: str) -> None:
        self.context.raise_if_cancelled()
        database.TASK_REPOSITORY.update_research(self.context.task_id, stage=name)
        task = database.get_task_record(self.context.task_id)
        record_trace(
            session_id=task["session_id"], task_id=self.context.task_id,
            event_type="research_stage", result_status="running",
            metrics={"stage": name},
        )


def create_research_task(request: ResearchTaskRequest) -> dict[str, Any]:
    result = enqueue_async_task({
        "session_id": request.session_id, "task_type": "research",
        "payload": {"message": request.query},
    })
    if not result["ok"]:
        raise ValueError("研究任务创建失败")
    task_id = result["data"]["task"]["task_id"]
    task = get_research_task(task_id)
    if task is None:
        raise ValueError("研究任务创建结果不可读取")
    record_trace(
        session_id=request.session_id, task_id=task_id, event_type="research_request",
        result_status=task["status"].lower(), metrics={"stage": task["progress"]["stage"]},
    )
    return task


def get_research_task(task_id: str) -> dict[str, Any] | None:
    task = database.get_task_record(task_id)
    if task is None:
        return None
    metadata = task["checkpoint_data"].get("async_task") or {}
    if metadata.get("task_type") != "research":
        return None
    stage = (metadata.get("progress_detail") or {}).get("stage", "CREATED")
    stored_status = task.get("task_status")
    status = {
        "created": "CREATED", "running": "RUNNING", "success": "COMPLETED",
        "failed": "FAILED", "cancelled": "CANCELLED", "paused": "PAUSED",
        "waiting_confirmation": "WAITING_CONFIRMATION", "partial_success": "PARTIAL_SUCCESS",
    }.get(stored_status, "CREATED")
    if status == "RUNNING" and stage in {"LOCAL_RETRIEVAL", "WEB_RETRIEVAL", "WAITING_TOOL"}:
        status = "WAITING_TOOL"
    if status in {"COMPLETED", "FAILED", "CANCELLED", "WAITING_CONFIRMATION"}:
        stage = status
    checkpoint = metadata.get("checkpoint") or {}
    result = checkpoint.get("result") if status in {"COMPLETED", "WAITING_CONFIRMATION"} else None
    if result is not None and status == "COMPLETED" and result.get("pending_action"):
        action = database.get_pending_action_record(result["pending_action"]["action_id"])
        result = {**result, "status": "completed", "pending_action": action}
    return {
        "id": task_id, "task_id": task_id, "session_id": task["session_id"],
        "status": status, "query": (metadata.get("payload") or {}).get("message", ""),
        "created_at": task["created_at"], "updated_at": task["updated_at"],
        "progress": {"stage": stage, "percent": 100 if status == "COMPLETED" else None},
        "result": result,
        "error": {"error_code": task.get("error_code") or "RESEARCH_FAILED",
                  "error_message": "研究任务执行失败，请重试或从检查点恢复"} if status == "FAILED" else None,
        "cancel_requested": bool(metadata.get("cancellation_requested")),
        "retry_count": int(metadata.get("retry_count", 0)),
    }


async def execute_research(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
    """One Async handler delegates to the existing Agent; results survive polling."""
    from backend.agent import run_agent

    observer = ResearchObserver(context)
    observer.stage("PLANNING")
    cached = context.checkpoint.get("result")
    if cached and cached.get("pending_action"):
        action = database.get_pending_action_record(cached["pending_action"]["action_id"])
        if action is None or action["status"] in {"cancelled", "failed"}:
            return {"ok": False, "data": None, "error_code": "RESEARCH_ACTION_TERMINATED",
                    "message": "原审批动作已结束，不能通过恢复任务重建动作"}
        if action["status"] == "executed":
            cached = {**cached, "status": "completed"}
            cached.pop("pending_action", None)
    result = cached or await run_agent(
        payload["message"], session_id=payload["session_id"], observer=observer,
    )
    if result.get("status") not in _SUCCESS_STATUSES:
        return {
            "ok": False, "data": None,
            "error_code": _ERROR_CODES.get(result.get("status"), "RESEARCH_AGENT_FAILED"),
            "message": "研究任务未完成，请检查任务状态后恢复",
        }
    safe_result = redact_value({
        key: result[key] for key in
        ("answer", "status", "task_id", "evidence", "unified_evidence", "pending_action")
        if key in result
    })
    database.TASK_REPOSITORY.update_research(context.task_id, result=safe_result)
    observer.stage("GENERATING")
    return {"ok": True, "data": safe_result, "error_code": None, "message": "研究结果已保存"}


def run_research_task(task_id: str) -> dict[str, Any]:
    """Sync worker entry; the existing Agent executes on the worker's loop."""
    return asyncio.run(run_async_task(task_id))


def _research_worker(payload: dict[str, Any], context: AsyncTaskContext) -> dict[str, Any]:
    return run_research_task(payload["task_id"])


async def dispatch_research_task(task_id: str) -> None:
    """Reuse Async Runtime's thread bridge so synchronous tools cannot block HTTP."""
    from backend.runtime.async_task_runtime import _run_sync_handler

    await _run_sync_handler(_research_worker, {"task_id": task_id}, AsyncTaskContext(task_id))
