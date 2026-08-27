"""Lightweight SQLite queue for bounded OCR, indexing, and Batch tasks."""

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import ValidationError

from backend import database
from backend.runtime.planner import PlannedStep, TaskPlan
from backend.runtime.policy import MAX_RETRIES
from backend.runtime.task_runner import StepStatus, start_task
from backend.schemas import AsyncTaskCreateRequest
from backend.services.batch_service import BatchArguments, run_batch
from backend.services.document_index import (
    index_document,
    ocr_document,
    reindex_document,
    reprocess_document,
)
from backend.services.file_locator import resolve_by_file_id
from backend.services.ocr_service import detect_pdf_type
from backend.services.task_view import get_task_view
from backend.services.trace_service import record_trace
from backend.tool_models import FileIdArguments
from backend.tools.excel_utils import failure, success


class AsyncTaskStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_CONFIRMATION = "waiting_confirmation"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
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
        metadata = ((task or {}).get("checkpoint_data") or {}).get("async_task") or {}
        return (
            task is None
            or task.get("task_status") == AsyncTaskStatus.CANCELLED.value
            or bool(metadata.get("cancellation_requested"))
        )

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

    def update_stage(
        self,
        stage: str,
        message: str,
        *,
        checkpoint: dict[str, Any] | None = None,
    ) -> None:
        """Publish a real stage when no defensible percentage exists."""
        self.raise_if_cancelled()
        self._update_metadata(
            message,
            progress_detail={"unit": "stage", "stage": str(stage)[:128]},
            checkpoint=checkpoint,
        )

    def update_counts(
        self,
        processed: int,
        total: int,
        message: str,
        *,
        unit: str,
        counts: dict[str, int] | None = None,
        checkpoint: dict[str, Any] | None = None,
    ) -> None:
        """Publish exact page/item/node counts and only derive a real percentage."""
        self.raise_if_cancelled()
        processed_value = max(0, int(processed))
        total_value = max(0, int(total))
        if total_value and processed_value > total_value:
            raise ValueError("processed 不能超过 total")
        keys = {
            "pages": ("processed_pages", "total_pages"),
            "items": ("processed_items", "total_items"),
            "nodes": ("completed_nodes", "total_nodes"),
        }
        if unit not in keys:
            raise ValueError("不支持的 progress unit")
        processed_key, total_key = keys[unit]
        detail: dict[str, Any] = {
            "unit": unit,
            processed_key: processed_value,
            total_key: total_value,
        }
        detail.update({key: max(0, int(value)) for key, value in (counts or {}).items()})
        progress = int(processed_value * 100 / total_value) if total_value else self.progress
        self._update_metadata(
            message,
            progress_detail=detail,
            checkpoint=checkpoint,
            progress=progress,
        )

    def enter_atomic(self, stage: str, message: str) -> None:
        self.raise_if_cancelled()
        self._update_metadata(
            message,
            progress_detail={"unit": "stage", "stage": stage},
            atomic_section=stage,
        )

    def leave_atomic(self, *, checkpoint: dict[str, Any] | None = None) -> None:
        """Persist completion of an atomic section before observing cancellation."""
        self._update_metadata(
            "原子操作已安全结束",
            checkpoint=checkpoint,
            atomic_section=None,
            check_cancel=False,
        )

    def _update_metadata(
        self,
        message: str,
        *,
        progress_detail: dict[str, Any] | None = None,
        checkpoint: dict[str, Any] | None = None,
        atomic_section: str | None = None,
        progress: int | None = None,
        check_cancel: bool = True,
    ) -> None:
        if check_cancel:
            self.raise_if_cancelled()
        task = _required_task(self.task_id)
        checkpoint_data = dict(task["checkpoint_data"])
        metadata = dict(checkpoint_data.get("async_task") or {})
        if progress_detail is not None:
            metadata["progress_detail"] = dict(progress_detail)
        if checkpoint is not None:
            metadata["checkpoint"] = dict(checkpoint)
        if atomic_section is None:
            metadata.pop("atomic_section", None)
        else:
            metadata["atomic_section"] = atomic_section
        checkpoint_data["async_task"] = metadata
        values: dict[str, Any] = {
            "message": _message(message),
            "checkpoint_data": checkpoint_data,
            "updated_at": database.utc_now(),
        }
        if progress is not None:
            values["progress"] = max(0, min(99, int(progress)))
        database.update_task_record(self.task_id, **values)


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
        "progress_detail": {"unit": "stage", "stage": "queued"},
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
        if inspect.iscoroutinefunction(handler):
            result = await handler(dict(metadata["payload"]), context)
        elif executor is not None:
            outcome = handler(dict(metadata["payload"]), context)
            result = await outcome if inspect.isawaitable(outcome) else outcome
        else:
            outcome = await _run_sync_handler(
                handler, dict(metadata["payload"]), context
            )
            result = await outcome if inspect.isawaitable(outcome) else outcome
        if not isinstance(result, dict):
            result = failure("ASYNC_TASK_RESULT_INVALID", "长任务返回结果无效")
        context.raise_if_cancelled()
    except AsyncTaskCancelled:
        return _finish_cancelled(clean_id, step)
    except Exception:
        result = failure("ASYNC_TASK_EXECUTION_ERROR", "异步任务执行失败")

    current = _required_task(clean_id)
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
        AsyncTaskStatus.PAUSED.value,
    }:
        return failure("TASK_NOT_CANCELLABLE", "当前任务状态不可取消")
    now = database.utc_now()
    if task["task_status"] == AsyncTaskStatus.RUNNING.value:
        checkpoint_data = dict(task["checkpoint_data"])
        metadata = dict(checkpoint_data.get("async_task") or {})
        metadata["cancellation_requested"] = True
        checkpoint_data["async_task"] = metadata
        atomic_section = metadata.get("atomic_section")
        database.update_task_record(
            task["task_id"],
            message=(
                f"取消请求已登记，等待原子阶段 {atomic_section} 安全结束"
                if atomic_section else "取消请求已登记，将在下一个安全点停止"
            ),
            checkpoint_data=checkpoint_data,
            updated_at=now,
        )
        return success(_task_payload(task["task_id"]), "取消请求已登记")
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
    """Retry only failed work when the handler exposed reliable failed units."""
    task = database.get_task_record(str(task_id).strip())
    if task is None or task.get("task_status") is None:
        return failure("TASK_NOT_FOUND", "未找到指定异步任务")
    if task["task_status"] not in {
        AsyncTaskStatus.FAILED.value,
        AsyncTaskStatus.PARTIAL_SUCCESS.value,
    }:
        return failure("TASK_NOT_RETRYABLE", "只有失败或部分成功任务可以重试")
    metadata = dict(task["checkpoint_data"].get("async_task") or {})
    retry_count = int(metadata.get("retry_count") or 0)
    if retry_count >= MAX_RETRIES:
        return failure("TASK_RETRY_LIMIT", "任务重试次数已达到上限")
    metadata["retry_count"] = retry_count + 1
    failed_pages = list((metadata.get("checkpoint") or {}).get("failed_pages") or [])
    failed_items = list((metadata.get("checkpoint") or {}).get("failed_items") or [])
    payload = dict(metadata.get("payload") or {})
    if metadata.get("task_type") == "ocr" and failed_pages:
        payload["pages"] = failed_pages
    elif metadata.get("task_type") == "batch" and failed_items:
        payload["student_ids"] = failed_items
    metadata["payload"] = payload
    metadata["checkpoint"] = {}
    metadata["progress_detail"] = {"unit": "stage", "stage": "queued_for_retry"}
    metadata.pop("cancellation_requested", None)
    return _reset_for_queue(task, metadata, progress=0, message="任务等待重试")


def resume_async_task(task_id: str) -> dict[str, Any]:
    """Resume a cancelled/failed task while retaining its durable checkpoint."""
    task = database.get_task_record(str(task_id).strip())
    if task is None or task.get("task_status") is None:
        return failure("TASK_NOT_FOUND", "未找到指定异步任务")
    if task["task_status"] not in {
        AsyncTaskStatus.CANCELLED.value,
        AsyncTaskStatus.FAILED.value,
        AsyncTaskStatus.PAUSED.value,
    }:
        return failure("TASK_NOT_RESUMABLE", "当前任务状态不可恢复")
    metadata = dict(task["checkpoint_data"].get("async_task") or {})
    metadata.pop("cancellation_requested", None)
    return _reset_for_queue(
        task,
        metadata,
        progress=int(task.get("progress") or 0),
        message="任务等待从检查点恢复",
    )


def _validated_payload(arguments: AsyncTaskCreateRequest) -> dict[str, Any]:
    if arguments.task_type in {"ocr", "layout", "index", "reindex"}:
        raw_payload = dict(arguments.payload)
        pages = raw_payload.pop("pages", None) if arguments.task_type == "ocr" else None
        payload = FileIdArguments.model_validate(raw_payload).model_dump()
        record = database.get_file_record_by_id(payload["file_id"])
        if record is None:
            raise ValueError("未找到指定文件")
        if arguments.task_type == "ocr" and record["file_type"] != "pdf":
            raise ValueError("OCR 任务只支持 PDF")
        if arguments.task_type in {"layout", "index", "reindex"} and record["file_type"] not in {"pdf", "word"}:
            raise ValueError("索引任务只支持 PDF 或 Word")
        if pages is not None:
            if (
                not isinstance(pages, list)
                or not pages
                or len(set(pages)) != len(pages)
                or any(not isinstance(page, int) or page < 1 for page in pages)
            ):
                raise ValueError("OCR pages 必须是唯一的正整数列表")
            payload["pages"] = pages
        return payload
    if arguments.task_type == "workflow":
        payload = dict(arguments.payload)
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > 10_000:
            raise ValueError("Workflow message 必须是有效的非空文本")
        return {"message": message.strip(), "session_id": arguments.session_id}
    payload = dict(arguments.payload)
    payload.setdefault("session_id", arguments.session_id)
    if payload.get("session_id") != arguments.session_id:
        raise ValueError("Batch session_id 必须与任务一致")
    return BatchArguments.model_validate(payload).model_dump()


def _default_executor(task_type: str) -> AsyncTaskExecutor:
    if task_type in {"ocr", "layout", "index", "reindex"}:
        def execute_document(
            payload: dict[str, Any], context: AsyncTaskContext
        ) -> dict[str, Any]:
            file_id = payload["file_id"]
            if task_type == "ocr":
                detected = detect_pdf_type(resolve_by_file_id(file_id))
                total_pages = int(((detected.get("data") or {}).get("page_count") or 0))
                selected_pages = payload.get("pages")
                if selected_pages is not None:
                    total_pages = len(selected_pages)
                context.update_counts(
                    0, total_pages, "OCR 页面等待处理", unit="pages",
                    checkpoint={"phase": "ocr", "processed_pages": []},
                )
                context.enter_atomic("ocr_index_activation", "OCR处理中；索引切换为原子阶段")
                result = ocr_document(file_id, pages=selected_pages)
            else:
                context.update_stage(task_type, f"正在执行 {task_type}")
                context.enter_atomic(
                    f"{task_type}_activation", f"{task_type} 正在构建并原子切换结果"
                )
                operation = {
                    "layout": reprocess_document,
                    "index": index_document,
                    "reindex": reindex_document,
                }[task_type]
                result = operation(file_id)
            result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
            checkpoint = {"phase": "persisted"}
            if task_type == "ocr":
                pages = result_data.get("page_results") or result_data.get("pages") or []
                if payload.get("pages") is not None:
                    selected = set(payload["pages"])
                    pages = [page for page in pages if page.get("page_no") in selected]
                failed_pages = list(result_data.get("failed_pages") or [])
                checkpoint.update(
                    {
                        "processed_pages": [page.get("page_no") for page in pages],
                        "failed_pages": failed_pages,
                    }
                )
            context.leave_atomic(checkpoint=checkpoint)
            context.raise_if_cancelled()
            if task_type == "ocr":
                pages = result_data.get("page_results") or result_data.get("pages") or []
                if payload.get("pages") is not None:
                    selected = set(payload["pages"])
                    pages = [page for page in pages if page.get("page_no") in selected]
                total = len(pages) or int(
                    (context.checkpoint or {}).get("total_pages") or 0
                )
                failed = len(result_data.get("failed_pages") or [])
                context.update_counts(
                    len(pages), total, "OCR 页面处理完成", unit="pages",
                    counts={
                        "success_count": max(0, len(pages) - failed),
                        "failed_count": failed,
                        "skipped_count": 0,
                    },
                    checkpoint=checkpoint,
                )
            else:
                context.update_stage("persisted", f"{task_type} 已安全保存", checkpoint=checkpoint)
            return result

        return execute_document
    if task_type == "batch":
        async def execute_batch(
            payload: dict[str, Any], context: AsyncTaskContext
        ) -> dict[str, Any]:
            context.update_stage("batch", "正在准备批量任务", checkpoint={"phase": "batch"})

            def progress(update: dict[str, Any]) -> None:
                context.update_counts(
                    update["processed_items"], update["total_items"],
                    "批量任务正在逐项处理", unit="items",
                    counts={
                        "success_count": update.get("success_count", 0),
                        "failed_count": update.get("failed_count", 0),
                        "skipped_count": update.get("skipped_count", 0),
                    },
                    checkpoint={
                        "phase": "batch",
                        "processed_items": update.get("processed_ids") or [],
                        "failed_items": update.get("failed_ids") or [],
                    },
                )

            result = await run_batch(
                payload,
                progress_callback=progress,
                cancel_check=context.raise_if_cancelled,
                skip_target_ids=set(context.checkpoint.get("processed_items") or []),
                parent_async_task_id=context.task_id,
            )
            context.raise_if_cancelled()
            return result

        return execute_batch
    if task_type == "workflow":
        async def execute_agent_workflow(
            payload: dict[str, Any], context: AsyncTaskContext
        ) -> dict[str, Any]:
            context.update_stage("planning", "Workflow 正在规划和执行")
            from backend.agent import run_agent

            result = await run_agent(
                payload["message"], session_id=payload["session_id"]
            )
            child_task_id = result.get("task_id")
            if child_task_id:
                _link_child_task(context.task_id, child_task_id)
                child = get_task_view(child_task_id)
                steps = (child or {}).get("steps") or []
                completed = sum(
                    step.get("status") in {"success", "confirmed", "cancelled"}
                    for step in steps
                )
                context.update_counts(
                    completed, len(steps), "Workflow Node 状态已同步", unit="nodes",
                    checkpoint={"child_task_id": child_task_id},
                )
            return {
                "ok": result.get("status") not in {"error", "runtime_error"},
                "data": result,
                "error_code": (
                    None if result.get("status") not in {"error", "runtime_error"}
                    else "WORKFLOW_RUNTIME_ERROR"
                ),
                "message": result.get("answer") or "Workflow 已执行",
            }

        return execute_agent_workflow
    raise ValueError("不支持的异步任务类型")


def _finish_task(
    task_id: str, step: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    ok = result.get("ok") is True
    now = database.utc_now()
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    status_hint = str(data.get("status") or "").casefold()
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    is_partial = status_hint == "partial_success" or (
        int(summary.get("success") or 0) > 0
        and int(summary.get("failed") or 0) > 0
    )
    is_waiting = (
        bool(data.get("pending_action"))
        or status_hint in {"waiting_confirmation", "confirmation_required"}
    )
    if not ok:
        task_status = AsyncTaskStatus.FAILED.value
    elif is_waiting:
        task_status = AsyncTaskStatus.WAITING_CONFIRMATION.value
    elif is_partial:
        task_status = AsyncTaskStatus.PARTIAL_SUCCESS.value
    else:
        task_status = AsyncTaskStatus.SUCCESS.value
    message = _message(
        result.get("message") or ("任务执行完成" if ok else "任务执行失败")
    )
    step_status = (
        StepStatus.WAITING_CONFIRMATION.value
        if is_waiting else StepStatus.SUCCESS.value if ok else StepStatus.FAILED.value
    )
    database.update_task_step_record(
        step["step_id"],
        status=step_status,
        result_summary=message,
        failed_reason=None if ok else message,
        completed_at=None if is_waiting else now,
    )
    current = _required_task(task_id)
    checkpoint_data = dict(current["checkpoint_data"])
    metadata = dict(checkpoint_data.get("async_task") or {})
    checkpoint = dict(metadata.get("checkpoint") or {})
    pending_action = data.get("pending_action")
    if isinstance(pending_action, dict) and pending_action.get("action_id"):
        checkpoint["action_id"] = str(pending_action["action_id"])
    child_task_id = data.get("task_id")
    if child_task_id and str(child_task_id) != task_id:
        checkpoint["child_task_id"] = str(child_task_id)
    if data.get("failed_pages"):
        checkpoint["failed_pages"] = list(data["failed_pages"])
    failure_details = data.get("failure_details") or []
    if failure_details:
        checkpoint["failed_items"] = [
            item.get("target_id") for item in failure_details if item.get("target_id")
        ]
    metadata["checkpoint"] = checkpoint
    metadata.pop("atomic_section", None)
    metadata.pop("cancellation_requested", None)
    checkpoint_data["async_task"] = metadata
    database.update_task_record(
        task_id,
        status=(
            "waiting_confirmation" if is_waiting
            else "success" if ok
            else "failed"
        ),
        task_status=task_status,
        progress=100 if ok and not is_waiting else int(current.get("progress") or 0),
        message=message,
        current_step=1 if is_waiting or not ok else None,
        next_action="等待用户确认" if is_waiting else None if ok else "可重试或从检查点恢复",
        checkpoint_data=checkpoint_data,
        updated_at=now,
        completed_at=None if is_waiting else now,
        error_code=None if ok else str(result.get("error_code") or "ASYNC_TASK_FAILED"),
    )
    task = _required_task(task_id)
    _trace(
        task,
        step,
        "async_task_waiting_confirmation" if is_waiting else "async_task_completed" if ok else "async_task_failed",
        task_status,
        result=result,
        error_code=task.get("error_code"),
    )
    if ok:
        return success(_task_payload(task_id), message)
    return failure(task["error_code"], message, _task_payload(task_id))


def _finish_cancelled(task_id: str, step: dict[str, Any]) -> dict[str, Any]:
    """Finalize cooperative cancellation only after the worker reaches a safe point."""
    now = database.utc_now()
    task = _required_task(task_id)
    checkpoint_data = dict(task["checkpoint_data"])
    metadata = dict(checkpoint_data.get("async_task") or {})
    metadata.pop("atomic_section", None)
    metadata.pop("cancellation_requested", None)
    checkpoint_data["async_task"] = metadata
    database.update_task_step_record(
        step["step_id"], status=StepStatus.CANCELLED.value,
        completed_at=now, result_summary="任务在安全点取消",
    )
    database.update_task_record(
        task_id, status=AsyncTaskStatus.CANCELLED.value,
        task_status=AsyncTaskStatus.CANCELLED.value,
        message="任务已在安全点取消", next_action="可从检查点恢复",
        checkpoint_data=checkpoint_data, updated_at=now, completed_at=now,
        error_code=None,
    )
    current = _required_task(task_id)
    _trace(current, step, "async_task_cancelled", AsyncTaskStatus.CANCELLED.value)
    return success(_task_payload(task_id), "任务已在安全点取消")


def synchronize_async_action(
    *,
    action_id: str,
    session_id: str,
    action_status: str,
    success_result: bool,
    result_message: str | None = None,
) -> dict[str, Any] | None:
    """Converge an outer async task after its durable HITL action terminates."""
    for task in database.list_task_records_for_session(session_id, 500):
        if task.get("task_status") != AsyncTaskStatus.WAITING_CONFIRMATION.value:
            continue
        metadata = dict((task.get("checkpoint_data") or {}).get("async_task") or {})
        checkpoint = dict(metadata.get("checkpoint") or {})
        if checkpoint.get("action_id") != action_id:
            continue
        steps = database.get_task_step_records(task["task_id"])
        if not steps:
            continue
        step = steps[0]
        now = database.utc_now()
        if action_status == "cancelled":
            task_status = AsyncTaskStatus.CANCELLED.value
            step_status = StepStatus.CANCELLED.value
            message = "用户已取消确认操作，任务已取消"
            error_code = None
        elif success_result:
            task_status = AsyncTaskStatus.SUCCESS.value
            step_status = StepStatus.SUCCESS.value
            message = "确认操作执行成功，任务已完成"
            error_code = None
        else:
            task_status = AsyncTaskStatus.FAILED.value
            step_status = StepStatus.FAILED.value
            message = _message(result_message or "确认操作执行失败")
            error_code = "ACTION_EXECUTION_FAILED"
        progress_detail = dict(metadata.get("progress_detail") or {})
        progress_detail["stage"] = task_status
        if task_status == AsyncTaskStatus.SUCCESS.value:
            if progress_detail.get("unit") == "nodes":
                progress_detail["completed_nodes"] = int(
                    progress_detail.get("total_nodes") or 0
                )
            progress_detail["percent"] = 100
        metadata["progress_detail"] = progress_detail
        checkpoint_data = dict(task.get("checkpoint_data") or {})
        checkpoint_data["async_task"] = metadata
        database.update_task_step_record(
            step["step_id"],
            status=step_status,
            result_summary=message if error_code is None else None,
            failed_reason=message if error_code is not None else None,
            completed_at=now,
        )
        database.update_task_record(
            task["task_id"],
            status=(
                "cancelled"
                if task_status == AsyncTaskStatus.CANCELLED.value
                else "success"
                if task_status == AsyncTaskStatus.SUCCESS.value
                else "failed"
            ),
            task_status=task_status,
            progress=(
                100
                if task_status == AsyncTaskStatus.SUCCESS.value
                else int(task.get("progress") or 0)
            ),
            message=message,
            current_step=None,
            next_action=None,
            checkpoint_data=checkpoint_data,
            updated_at=now,
            completed_at=now,
            error_code=error_code,
        )
        current = _required_task(task["task_id"])
        _trace(
            current,
            step,
            "async_task_action_completed",
            task_status,
            result={"action_id": action_id, "action_status": action_status},
            error_code=error_code,
        )
        return current
    return None


def _link_child_task(parent_task_id: str, child_task_id: str) -> None:
    """Mark an implementation-detail child so Task Center shows one logical task."""
    child = database.get_task_record(child_task_id)
    if child is None:
        return
    checkpoint_data = dict(child.get("checkpoint_data") or {})
    checkpoint_data["parent_async_task_id"] = parent_task_id
    database.update_task_record(
        child_task_id,
        checkpoint_data=checkpoint_data,
        updated_at=database.utc_now(),
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
    view = get_task_view(task_id)
    if view is None:
        raise ValueError("Task 不存在")
    return view


def _required_task(task_id: str) -> dict[str, Any]:
    task = database.get_task_record(task_id)
    if task is None:
        raise ValueError("Task 不存在")
    return task


def _message(value: Any) -> str:
    return str(value or "")[:500]


async def _run_sync_handler(
    handler: AsyncTaskExecutor,
    payload: dict[str, Any],
    context: AsyncTaskContext,
) -> Any:
    """Keep the event loop responsive without relying on a broker or loop callbacks."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="async-task")
    future = pool.submit(handler, payload, context)
    try:
        while not future.done():
            await asyncio.sleep(0.01)
        return future.result()
    finally:
        pool.shutdown(wait=True, cancel_futures=False)


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
        current_steps = database.get_task_step_records(task["task_id"])
        current_step = next(
            (item for item in current_steps if item["step_id"] == step["step_id"]),
            step,
        )
        started_at = current_step.get("started_at")
        completed_at = current_step.get("completed_at") or task.get("completed_at")
        async_meta = (task.get("checkpoint_data") or {}).get("async_task") or {}
        timing = _async_timing(task.get("created_at"), started_at, completed_at)
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
            metrics={
                "stage": event_type,
                "queue_time_ms": timing["queue_time_ms"],
                "execution_time_ms": timing["execution_time_ms"],
                "task_total_duration_ms": timing["task_total_duration_ms"],
                "progress_detail": async_meta.get("progress_detail") or {},
            },
        )
    except Exception:
        pass


def _async_timing(
    created_at: Any, started_at: Any, completed_at: Any
) -> dict[str, int | None]:
    created = _timestamp(created_at)
    started = _timestamp(started_at)
    completed = _timestamp(completed_at)
    return {
        "queue_time_ms": _delta_ms(created, started),
        "execution_time_ms": _delta_ms(started, completed),
        "task_total_duration_ms": _delta_ms(created, completed),
    }


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _delta_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))
