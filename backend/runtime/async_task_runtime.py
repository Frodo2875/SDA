"""Lightweight SQLite queue for bounded OCR, indexing, and Batch tasks."""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import ValidationError

from backend import database
from backend.runtime.planner import PlannedStep, TaskPlan
from backend.runtime.policy import MAX_RETRIES
from backend.runtime.task_runner import StepStatus, start_task
from backend.schemas import AsyncTaskCreateRequest
from backend.services.batch_service import BatchArguments, run_batch
from backend.services.document_index import index_document
from backend.services.trace_service import record_trace
from backend.tool_models import FileIdArguments
from backend.tools.excel_utils import failure, success


class AsyncTaskStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AsyncTaskCancelled(RuntimeError):
    """Raised by cooperative workers after a persisted cancellation request."""


@dataclass
class AsyncTaskContext:
    """Controlled progress/checkpoint API exposed to Python task handlers."""

    task_id: str

    @property
    def checkpoint(self) -> dict[str, Any]:
        task = _required_task(self.task_id)
        metadata = task["checkpoint_data"].get("async_task") or {}
        return dict(metadata.get("checkpoint") or {})

    @property
    def progress(self) -> int:
        return int(_required_task(self.task_id).get("progress") or 0)

    def is_cancelled(self) -> bool:
        task = database.get_task_record(self.task_id)
        return task is None or task.get("task_status") == AsyncTaskStatus.CANCELLED.value

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise AsyncTaskCancelled("任务已取消")

    def update_progress(
        self,
        progress: int,
        message: str,
        *,
        checkpoint: dict[str, Any] | None = None,
    ) -> None:
        self.raise_if_cancelled()
        task = _required_task(self.task_id)
        value = int(progress)
        if not 0 <= value <= 99:
            raise ValueError("运行中 progress 必须在 0 到 99 之间")
        if value < int(task.get("progress") or 0):
            raise ValueError("progress 不能倒退")
        checkpoint_data = dict(task["checkpoint_data"])
        metadata = dict(checkpoint_data.get("async_task") or {})
        if checkpoint is not None:
            metadata["checkpoint"] = dict(checkpoint)
        checkpoint_data["async_task"] = metadata
        database.update_task_record(
            self.task_id,
            progress=value,
            message=_message(message),
            checkpoint_data=checkpoint_data,
            updated_at=database.utc_now(),
        )


AsyncTaskExecutor = Callable[
    [dict[str, Any], AsyncTaskContext],
    dict[str, Any] | Awaitable[dict[str, Any]],
]


def enqueue_async_task(
    request: AsyncTaskCreateRequest | dict[str, Any],
) -> dict[str, Any]:
    """Validate and persist one allow-listed task without executing it inline."""
    try:
        arguments = (
            request
            if isinstance(request, AsyncTaskCreateRequest)
            else AsyncTaskCreateRequest.model_validate(request)
        )
        payload = _validated_payload(arguments)
    except (ValidationError, ValueError) as exc:
        return failure("INVALID_ASYNC_TASK", f"异步任务参数无效：{exc}")

    tool_name = f"async_{arguments.task_type}"
    plan = TaskPlan(
        task_type=tool_name,
        steps=(
            PlannedStep(
                1,
                f"执行{arguments.task_type}长任务",
                "ANALYSIS",
                tool_name,
                input=payload,
                expected_output="持久化的长任务结果",
            ),
        ),
    )
    task = start_task(
        session_id=arguments.session_id,
        user_message=f"Async task: {arguments.task_type}",
        plan=plan,
    )
    checkpoint_data = dict(task["checkpoint_data"])
    checkpoint_data["async_task"] = {
        "task_type": arguments.task_type,
        "payload": payload,
        "checkpoint": {},
        "retry_count": 0,
    }
    database.update_task_record(
        task["task_id"],
        status="pending",
        task_status=AsyncTaskStatus.CREATED.value,
        progress=0,
        message="任务已进入队列",
        current_step=1,
        next_action=plan.steps[0].step_name,
        checkpoint_data=checkpoint_data,
        updated_at=database.utc_now(),
    )
    queued = _task_payload(task["task_id"])
    _trace(queued["task"], queued["steps"][0], "async_task_queued", "created")
    return success(queued, "异步任务已创建")


def get_async_task(task_id: str) -> dict[str, Any]:
    task = database.get_task_record(str(task_id).strip())
    if task is None or task.get("task_status") is None:
        return failure("TASK_NOT_FOUND", "未找到指定异步任务")
    return success(_task_payload(task["task_id"]), "异步任务状态读取成功")


async def run_async_task(
    task_id: str,
    *,
    executor: AsyncTaskExecutor | None = None,
) -> dict[str, Any]:
    """Claim and execute one task; successful or cancelled work is never replayed."""
    clean_id = str(task_id).strip()
    claimed = database.claim_async_task_record(clean_id)
    if claimed is None:
        current = database.get_task_record(clean_id)
        if current is None or current.get("task_status") is None:
            return failure("TASK_NOT_FOUND", "未找到指定异步任务")
        if current["task_status"] in {
            AsyncTaskStatus.SUCCESS.value,
            AsyncTaskStatus.CANCELLED.value,
        }:
            return success(_task_payload(clean_id), "任务已处于终止状态")
        return failure("TASK_NOT_RUNNABLE", "任务当前不可执行", _task_payload(clean_id))

    step = database.get_task_step_records(clean_id)[0]
    now = database.utc_now()
    database.update_task_step_record(
        step["step_id"],
        status=StepStatus.RUNNING.value,
        started_at=step.get("started_at") or now,
        completed_at=None,
        failed_reason=None,
    )
    _trace(claimed, step, "async_task_started", AsyncTaskStatus.RUNNING.value)
    context = AsyncTaskContext(clean_id)
    metadata = claimed["checkpoint_data"]["async_task"]
    handler = executor or _default_executor(metadata["task_type"])
    try:
        outcome = handler(dict(metadata["payload"]), context)
        result = await outcome if inspect.isawaitable(outcome) else outcome
        if not isinstance(result, dict):
            result = failure("ASYNC_TASK_RESULT_INVALID", "长任务返回结果无效")
    except AsyncTaskCancelled:
        return success(_task_payload(clean_id), "任务已取消")
    except Exception:
        result = failure("ASYNC_TASK_EXECUTION_ERROR", "异步任务执行失败")

    current = _required_task(clean_id)
    if current["task_status"] == AsyncTaskStatus.CANCELLED.value:
        return success(_task_payload(clean_id), "任务已取消")
    return _finish_task(clean_id, step, result)


async def run_next_async_task(
    *, executor: AsyncTaskExecutor | None = None
) -> dict[str, Any]:
    """Run the oldest queued task, suitable for a simple polling worker."""
    queued = next(
        (
            row
            for row in database.fetch_all("tasks")
            if row.get("task_status") == AsyncTaskStatus.CREATED.value
            and row.get("status") == "pending"
        ),
        None,
    )
    if queued is None:
        return success(None, "当前没有待执行任务")
    return await run_async_task(queued["task_id"], executor=executor)


def cancel_async_task(task_id: str) -> dict[str, Any]:
    task = database.get_task_record(str(task_id).strip())
    if task is None or task.get("task_status") is None:
        return failure("TASK_NOT_FOUND", "未找到指定异步任务")
    if task["task_status"] not in {
        AsyncTaskStatus.CREATED.value,
        AsyncTaskStatus.RUNNING.value,
    }:
        return failure("TASK_NOT_CANCELLABLE", "当前任务状态不可取消")
    now = database.utc_now()
    database.update_task_record(
        task["task_id"],
        status="cancelled",
        task_status=AsyncTaskStatus.CANCELLED.value,
        message="任务已取消",
        next_action="可从检查点恢复",
        updated_at=now,
        completed_at=now,
        error_code=None,
    )
    step = database.get_task_step_records(task["task_id"])[0]
    if step["status"] != StepStatus.SUCCESS.value:
        database.update_task_step_record(
            step["step_id"],
            status=StepStatus.CANCELLED.value,
            completed_at=now,
            result_summary="任务已取消",
        )
    _trace(
        _required_task(task["task_id"]),
        step,
        "async_task_cancelled",
        AsyncTaskStatus.CANCELLED.value,
    )
    return success(_task_payload(task["task_id"]), "异步任务已取消")


def retry_async_task(task_id: str) -> dict[str, Any]:
    """Explicitly retry a failed task from the beginning with a strict ceiling."""
    task = database.get_task_record(str(task_id).strip())
    if task is None or task.get("task_status") is None:
        return failure("TASK_NOT_FOUND", "未找到指定异步任务")
    if task["task_status"] != AsyncTaskStatus.FAILED.value:
        return failure("TASK_NOT_RETRYABLE", "只有失败任务可以重试")
    metadata = dict(task["checkpoint_data"].get("async_task") or {})
    retry_count = int(metadata.get("retry_count") or 0)
    if retry_count >= MAX_RETRIES:
        return failure("TASK_RETRY_LIMIT", "任务重试次数已达到上限")
    metadata["retry_count"] = retry_count + 1
    metadata["checkpoint"] = {}
    return _reset_for_queue(task, metadata, progress=0, message="任务等待重试")


def resume_async_task(task_id: str) -> dict[str, Any]:
    """Resume a cancelled/failed task while retaining its durable checkpoint."""
    task = database.get_task_record(str(task_id).strip())
    if task is None or task.get("task_status") is None:
        return failure("TASK_NOT_FOUND", "未找到指定异步任务")
    if task["task_status"] not in {
        AsyncTaskStatus.CANCELLED.value,
        AsyncTaskStatus.FAILED.value,
    }:
        return failure("TASK_NOT_RESUMABLE", "当前任务状态不可恢复")
    metadata = dict(task["checkpoint_data"].get("async_task") or {})
    return _reset_for_queue(
        task,
        metadata,
        progress=int(task.get("progress") or 0),
        message="任务等待从检查点恢复",
    )


def _validated_payload(arguments: AsyncTaskCreateRequest) -> dict[str, Any]:
    if arguments.task_type in {"ocr", "index"}:
        payload = FileIdArguments.model_validate(arguments.payload).model_dump()
        record = database.get_file_record_by_id(payload["file_id"])
        if record is None:
            raise ValueError("未找到指定文件")
        if arguments.task_type == "ocr" and record["file_type"] != "pdf":
            raise ValueError("OCR 任务只支持 PDF")
        if arguments.task_type == "index" and record["file_type"] not in {"pdf", "word"}:
            raise ValueError("索引任务只支持 PDF 或 Word")
        return payload
    payload = dict(arguments.payload)
    payload.setdefault("session_id", arguments.session_id)
    if payload.get("session_id") != arguments.session_id:
        raise ValueError("Batch session_id 必须与任务一致")
    return BatchArguments.model_validate(payload).model_dump()


def _default_executor(task_type: str) -> AsyncTaskExecutor:
    if task_type in {"ocr", "index"}:
        def execute_document(
            payload: dict[str, Any], context: AsyncTaskContext
        ) -> dict[str, Any]:
            context.update_progress(
                max(context.progress, 10),
                "正在准备文档处理",
                checkpoint={"phase": "processing"},
            )
            result = index_document(payload["file_id"])
            context.raise_if_cancelled()
            context.update_progress(
                max(context.progress, 90),
                "文档处理完成，正在保存结果",
                checkpoint={"phase": "persisted"},
            )
            return result

        return execute_document
    if task_type == "batch":
        async def execute_batch(
            payload: dict[str, Any], context: AsyncTaskContext
        ) -> dict[str, Any]:
            context.update_progress(
                max(context.progress, 5),
                "正在执行批量任务",
                checkpoint={"phase": "batch"},
            )
            result = await run_batch(payload)
            context.raise_if_cancelled()
            context.update_progress(
                max(context.progress, 90),
                "批量处理完成，正在汇总",
                checkpoint={"phase": "persisted"},
            )
            return result

        return execute_batch
    raise ValueError("不支持的异步任务类型")


def _finish_task(
    task_id: str, step: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    ok = result.get("ok") is True
    now = database.utc_now()
    task_status = AsyncTaskStatus.SUCCESS.value if ok else AsyncTaskStatus.FAILED.value
    message = _message(
        result.get("message") or ("任务执行完成" if ok else "任务执行失败")
    )
    database.update_task_step_record(
        step["step_id"],
        status=StepStatus.SUCCESS.value if ok else StepStatus.FAILED.value,
        result_summary=message,
        failed_reason=None if ok else message,
        completed_at=now,
    )
    database.update_task_record(
        task_id,
        status="success" if ok else "failed",
        task_status=task_status,
        progress=100 if ok else int(_required_task(task_id).get("progress") or 0),
        message=message,
        current_step=None if ok else 1,
        next_action=None if ok else "可重试或从检查点恢复",
        updated_at=now,
        completed_at=now,
        error_code=None if ok else str(result.get("error_code") or "ASYNC_TASK_FAILED"),
    )
    task = _required_task(task_id)
    _trace(
        task,
        step,
        "async_task_completed" if ok else "async_task_failed",
        task_status,
        result=result,
        error_code=task.get("error_code"),
    )
    return (
        success(_task_payload(task_id), "异步任务执行完成")
        if ok
        else failure(task["error_code"], message, _task_payload(task_id))
    )


def _reset_for_queue(
    task: dict[str, Any],
    metadata: dict[str, Any],
    *,
    progress: int,
    message: str,
) -> dict[str, Any]:
    checkpoint_data = dict(task["checkpoint_data"])
    checkpoint_data["async_task"] = metadata
    now = database.utc_now()
    step = database.get_task_step_records(task["task_id"])[0]
    database.update_task_step_record(
        step["step_id"],
        status=StepStatus.CREATED.value,
        retry_count=int(metadata.get("retry_count") or 0),
        result_summary=None,
        failed_reason=None,
        started_at=None,
        completed_at=None,
    )
    database.update_task_record(
        task["task_id"],
        status="pending",
        task_status=AsyncTaskStatus.CREATED.value,
        progress=progress,
        message=message,
        current_step=1,
        next_action=step["step_name"],
        checkpoint_data=checkpoint_data,
        updated_at=now,
        completed_at=None,
        error_code=None,
    )
    return success(_task_payload(task["task_id"]), message)


def _task_payload(task_id: str) -> dict[str, Any]:
    return {
        "task": _required_task(task_id),
        "steps": database.get_task_step_records(task_id),
    }


def _required_task(task_id: str) -> dict[str, Any]:
    task = database.get_task_record(task_id)
    if task is None:
        raise ValueError("Task 不存在")
    return task


def _message(value: Any) -> str:
    return str(value or "")[:500]


def _trace(
    task: dict[str, Any],
    step: dict[str, Any],
    event_type: str,
    result_status: str,
    *,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> None:
    try:
        record_trace(
            task_id=task["task_id"],
            session_id=task["session_id"],
            step_id=step["step_id"],
            event_type=event_type,
            tool_name=step.get("tool_name"),
            arguments={"task_type": task["task_type"], "progress": task.get("progress")},
            result=result or {"status": result_status, "message": task.get("message")},
            result_status=result_status,
            error_code=error_code,
        )
    except Exception:
        pass
