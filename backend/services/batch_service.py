"""Deterministic Batch iteration, partial failures, and one confirmed write preview."""

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from backend import database
from backend.llm_client import LLMClient
from backend.runtime.planner import PlannedStep, TaskPlan
from backend.runtime.task_runner import finalize_task, record_tool_execution, start_task
from backend.services.confirmation import create_pending_action
from backend.tools.data_quality_tools import validate_student_data
from backend.tools.excel_utils import failure, read_excel_rows, success
from backend.tools.student_tools import (
    STUDENT_FIELDS,
    STUDENT_FILE,
    get_student_info,
    get_student_research,
    get_student_scores,
)
from backend.tools.table_tools import aggregate_table


BatchActionType = Literal[
    "score_below_threshold",
    "score_below_threshold_no_research",
    "college_count",
    "data_quality",
    "generate_evaluations",
]


class BatchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    session_id: str = Field(min_length=1, max_length=128)
    action_type: BatchActionType
    threshold: float = Field(default=70, ge=0, le=100)
    student_ids: list[str] | None = Field(default=None, max_length=1000)
    file_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{32}$")
    sheet: str | None = Field(default=None, min_length=1, max_length=128)
    college_field: str = Field(default="学院", min_length=1, max_length=128)
    target_file: str = Field(default="综合评价.docx", min_length=1, max_length=255)

    @field_validator("student_ids")
    @classmethod
    def validate_student_ids(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        cleaned = [str(value).strip() for value in values]
        if not cleaned or any(not value for value in cleaned):
            raise ValueError("student_ids 不能为空")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("student_ids 不能重复")
        return cleaned

    @model_validator(mode="after")
    def validate_action_fields(self) -> "BatchArguments":
        if self.action_type == "college_count" and (not self.file_id or not self.sheet):
            raise ValueError("按学院统计必须提供 file_id 和 sheet")
        if self.action_type == "generate_evaluations" and not self.target_file.endswith(".docx"):
            raise ValueError("批量评价目标必须是 .docx 文件")
        return self


class BatchChatClient(Protocol):
    async def create_chat_completion(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


EvaluationGenerator = Callable[[dict[str, Any]], str | Awaitable[str]]
BatchProgressCallback = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], None]


async def run_batch(
    arguments: BatchArguments | dict[str, Any],
    *,
    client: BatchChatClient | None = None,
    evaluation_generator: EvaluationGenerator | None = None,
    progress_callback: BatchProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
    skip_target_ids: set[str] | None = None,
    parent_async_task_id: str | None = None,
) -> dict[str, Any]:
    """Run one bounded Batch while persisting progress after every Python item."""
    try:
        args = (
            arguments
            if isinstance(arguments, BatchArguments)
            else BatchArguments.model_validate(arguments)
        )
    except ValidationError as exc:
        return failure("INVALID_BATCH_ARGUMENTS", f"Batch 参数无效：{exc}")

    task = _start_batch_task(args)
    if parent_async_task_id is not None:
        checkpoint_data = dict(task.get("checkpoint_data") or {})
        checkpoint_data["parent_async_task_id"] = parent_async_task_id
        database.update_task_record(
            task["task_id"],
            checkpoint_data=checkpoint_data,
            updated_at=database.utc_now(),
        )
    batch_id = uuid4().hex
    database.create_batch_record(
        {
            "batch_id": batch_id,
            "session_id": args.session_id,
            "task_id": task["task_id"],
            "action_type": args.action_type,
            "status": "pending",
            "total": 0,
            "success_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "created_at": database.utc_now(),
            "completed_at": None,
        }
    )
    database.update_batch_record(batch_id, status="running")

    if args.action_type == "college_count":
        return _run_college_count(
            batch_id, task["task_id"], args,
            progress_callback=progress_callback, cancel_check=cancel_check,
        )

    targets_result = _student_targets(args.student_ids)
    if not targets_result["ok"]:
        return _fail_batch_start(batch_id, task["task_id"], targets_result)
    skipped_ids = set(skip_target_ids or set())
    targets = [
        target for target in targets_result["data"]
        if target["student_id"] not in skipped_ids
    ]
    database.update_batch_record(batch_id, total=len(targets))

    generated: list[dict[str, str]] = []
    processed_ids: list[str] = []
    failed_ids: list[str] = []
    for target in targets:
        if cancel_check is not None:
            _cooperative_call(cancel_check, batch_id, task["task_id"])
        if args.action_type == "score_below_threshold":
            outcome = _score_outcome(target, args.threshold, require_no_research=False)
        elif args.action_type == "score_below_threshold_no_research":
            outcome = _score_outcome(target, args.threshold, require_no_research=True)
        elif args.action_type == "data_quality":
            outcome = _quality_outcome(target)
        else:
            outcome = await _evaluation_outcome(
                target,
                client=client,
                generator=evaluation_generator,
            )
            if outcome["status"] == "success":
                generated.append(
                    {"student_id": target["student_id"], "content": outcome["content"]}
                )
        _persist_item(batch_id, target["student_id"], outcome)
        processed_ids.append(target["student_id"])
        if outcome["status"] == "failed":
            failed_ids.append(target["student_id"])
        if progress_callback is not None:
            current = database.get_batch_record(batch_id)
            _cooperative_call(lambda: progress_callback(
                {
                    "processed_items": len(processed_ids),
                    "total_items": len(targets),
                    "processed_ids": list(processed_ids),
                    "failed_ids": list(failed_ids),
                    "success_count": current["success_count"],
                    "failed_count": current["failed_count"],
                    "skipped_count": current["skipped_count"],
                }
            ), batch_id, task["task_id"])

    if args.action_type == "generate_evaluations":
        return _prepare_batch_write(
            batch_id=batch_id,
            task_id=task["task_id"],
            args=args,
            generated=generated,
        )
    return _finish_read_batch(batch_id, task["task_id"])


def get_batch(batch_id: str) -> dict[str, Any]:
    batch = database.get_batch_record(str(batch_id).strip())
    if batch is None:
        return failure("BATCH_NOT_FOUND", "未找到指定 Batch")
    return success(_batch_payload(batch), "Batch 状态读取成功")


def _start_batch_task(args: BatchArguments) -> dict[str, Any]:
    if args.action_type == "generate_evaluations":
        steps = (
            PlannedStep(1, "批量生成综合评价", "tool", "batch_generate_evaluations"),
            PlannedStep(2, "生成 Word Diff 预览", "tool", "preview_word_diff"),
            PlannedStep(3, "等待用户确认", "confirmation"),
            PlannedStep(4, "执行整批确认写入", "side_effect", "write_word"),
        )
    else:
        steps = (PlannedStep(1, "执行批量 Python 任务", "tool", args.action_type),)
    return start_task(
        session_id=args.session_id,
        user_message=f"Batch: {args.action_type}",
        plan=TaskPlan(task_type=f"batch_{args.action_type}", steps=steps),
    )


def _student_targets(student_ids: list[str] | None) -> dict[str, Any]:
    result = read_excel_rows(STUDENT_FILE, STUDENT_FIELDS)
    if not result["ok"]:
        return result
    by_id = {
        str(row.get("学号") or "").strip(): {
            "student_id": str(row.get("学号") or "").strip(),
            "name": str(row.get("姓名") or "").strip(),
        }
        for row in result["data"]
    }
    if student_ids is None:
        return success(list(by_id.values()), "读取全部学生成功")
    return success(
        [by_id.get(student_id, {"student_id": student_id, "name": ""}) for student_id in student_ids],
        "读取指定学生成功",
    )


def _score_outcome(
    target: dict[str, str], threshold: float, *, require_no_research: bool
) -> dict[str, Any]:
    score = get_student_scores(target["student_id"])
    if not score["ok"]:
        return _failed(score.get("error_code") or "SCORE_QUERY_FAILED", score["message"])
    if score["data"].get("status") != "found":
        return _failed("SCORE_NO_RECORD", "当前成绩材料中未查询到相关记录")
    average = float(score["data"]["average_score"])
    if average >= threshold:
        return _skipped(f"平均分 {average} 不低于阈值 {threshold}")
    if require_no_research:
        research = get_student_research(target["student_id"])
        if not research["ok"]:
            return _failed(
                research.get("error_code") or "RESEARCH_QUERY_FAILED", research["message"]
            )
        if research["data"].get("status") != "no_record":
            return _skipped(f"平均分 {average}，但科研材料存在该学生记录")
        return _succeeded(f"平均分 {average}；当前科研成果材料中未查询到相关记录")
    return _succeeded(f"平均分 {average} 低于阈值 {threshold}")


def _quality_outcome(target: dict[str, str]) -> dict[str, Any]:
    result = validate_student_data(target["student_id"])
    if not result["ok"]:
        return _failed(result.get("error_code") or "QUALITY_CHECK_FAILED", result["message"])
    status = str(result["data"].get("status") or "unknown")
    issue_count = len(result["data"].get("issues") or [])
    return _succeeded(f"质量状态：{status}；问题数：{issue_count}")


async def _evaluation_outcome(
    target: dict[str, str],
    *,
    client: BatchChatClient | None,
    generator: EvaluationGenerator | None,
) -> dict[str, Any]:
    info = get_student_info(target["student_id"])
    scores = get_student_scores(target["student_id"])
    research = get_student_research(target["student_id"])
    for label, result in (("基本信息", info), ("成绩", scores), ("科研", research)):
        if not result["ok"]:
            return _failed(result.get("error_code") or "SOURCE_QUERY_FAILED", f"{label}查询失败：{result['message']}")
    if scores["data"].get("status") != "found":
        return _failed("SCORE_NO_RECORD", "当前成绩材料中未查询到相关记录")
    context = {
        "student": info["data"],
        "scores": scores["data"],
        "research": research["data"],
    }
    try:
        if generator is not None:
            generated = generator(context)
            content = await generated if inspect.isawaitable(generated) else generated
        else:
            content = await _llm_evaluation(context, client or LLMClient())
    except Exception as exc:
        return _failed("EVALUATION_GENERATION_FAILED", f"评价生成失败：{exc}")
    if not isinstance(content, str) or not content.strip():
        return _failed("INVALID_EVALUATION", "评价生成结果为空")
    return {**_succeeded(content.strip()), "content": content.strip()}


async def _llm_evaluation(context: dict[str, Any], client: BatchChatClient) -> str:
    response = await client.create_chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "根据提供的 Python 工具结构化结果生成一段简洁中文综合评价。"
                    "不得补充未提供的数据；科研 status=no_record 时必须表述为"
                    "当前科研成果材料中未查询到相关记录。"
                ),
            },
            {"role": "user", "content": str(context)},
        ],
        [],
    )
    if response.get("tool_calls"):
        raise ValueError("批量评价生成阶段不接受额外 Tool Calling")
    return str(response.get("content") or "").strip()


def _run_college_count(
    batch_id: str,
    task_id: str,
    args: BatchArguments,
    *,
    progress_callback: BatchProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    result = aggregate_table(
        args.file_id,
        args.sheet,
        operation="count",
        group_by=[args.college_field],
    )
    if not result["ok"]:
        return _fail_batch_start(batch_id, task_id, result)
    groups = result["data"].get("results") or []
    database.update_batch_record(batch_id, total=len(groups))
    for index, group in enumerate(groups, start=1):
        if cancel_check is not None:
            _cooperative_call(cancel_check, batch_id, task_id)
        target = str(group.get(args.college_field))
        _persist_item(batch_id, target, _succeeded(f"人数：{group.get('value')}"))
        if progress_callback is not None:
            _cooperative_call(lambda: progress_callback(
                {
                    "processed_items": index,
                    "total_items": len(groups),
                    "processed_ids": [
                        str(item.get(args.college_field)) for item in groups[:index]
                    ],
                    "failed_ids": [],
                    "success_count": index,
                    "failed_count": 0,
                    "skipped_count": 0,
                }
            ), batch_id, task_id)
    return _finish_read_batch(batch_id, task_id)


def _prepare_batch_write(
    *, batch_id: str, task_id: str, args: BatchArguments,
    generated: list[dict[str, str]],
) -> dict[str, Any]:
    current = database.get_batch_record(batch_id)
    tool_result = success(
        {"generated_count": len(generated), "failed_count": current["failed_count"]},
        "批量评价逐项生成完成",
    )
    record_tool_execution(
        task_id=task_id,
        tool_name="batch_generate_evaluations",
        arguments={"student_ids": [item["student_id"] for item in generated]},
        result=tool_result,
        retry_count=0,
    )
    if not generated:
        database.update_batch_record(
            batch_id, status="failed", completed_at=database.utc_now()
        )
        finalize_task(task_id, {"status": "batch_failed"})
        return failure("BATCH_ALL_ITEMS_FAILED", "没有可写入的综合评价", _batch_payload(database.get_batch_record(batch_id)))
    content = "\n\n".join(
        f"{item['student_id']} 综合评价\n{item['content']}" for item in generated
    )
    pending = create_pending_action(
        session_id=args.session_id,
        target_file=args.target_file,
        student_id=f"BATCH:{batch_id}",
        student_name="批量综合评价",
        content=content,
        task_id=task_id,
    )
    if not pending["ok"]:
        database.reclassify_batch_success_items(
            batch_id, status="failed",
            error_code=pending.get("error_code") or "BATCH_PREVIEW_FAILED",
            summary=pending["message"],
        )
        counts = _counts(database.get_batch_record(batch_id)["items"])
        database.update_batch_record(
            batch_id, status="failed", completed_at=database.utc_now(), **counts
        )
        finalize_task(task_id, {"status": "batch_preview_failed"})
        return failure(
            pending.get("error_code") or "BATCH_PREVIEW_FAILED",
            pending["message"],
            _batch_payload(database.get_batch_record(batch_id)),
        )
    action = pending["data"]
    database.link_batch_action(batch_id, action["action_id"])
    database.update_batch_record(batch_id, status="pending")
    finalize_task(
        task_id,
        {"status": "confirmation_required", "pending_action": action},
    )
    batch = database.get_batch_record(batch_id)
    return success(
        {**_batch_payload(batch), "pending_action": action},
        "批量评价已生成并完成 Diff 预览，等待用户确认",
    )


def _finish_read_batch(batch_id: str, task_id: str) -> dict[str, Any]:
    batch = database.get_batch_record(batch_id)
    status = _final_status(batch)
    database.update_batch_record(
        batch_id, status=status, completed_at=database.utc_now()
    )
    batch = database.get_batch_record(batch_id)
    tool_result = success(_batch_payload(batch), "批量 Python 任务执行完成")
    record_tool_execution(
        task_id=task_id,
        tool_name=batch["action_type"],
        arguments={"total": batch["total"]},
        result=tool_result if status != "failed" else failure("BATCH_ALL_ITEMS_FAILED", "全部 item 失败"),
        retry_count=0,
    )
    finalize_task(task_id, {"status": "completed" if status != "failed" else "batch_failed"})
    return tool_result if status != "failed" else failure(
        "BATCH_ALL_ITEMS_FAILED", "全部 Batch item 执行失败", _batch_payload(batch)
    )


def _fail_batch_start(batch_id: str, task_id: str, result: dict[str, Any]) -> dict[str, Any]:
    database.update_batch_record(
        batch_id, status="failed", completed_at=database.utc_now(), failed_count=0
    )
    record_tool_execution(
        task_id=task_id,
        tool_name=database.get_batch_record(batch_id)["action_type"],
        arguments={}, result=result, retry_count=0,
    )
    finalize_task(task_id, {"status": "batch_failed"})
    return failure(result.get("error_code") or "BATCH_START_FAILED", result["message"], _batch_payload(database.get_batch_record(batch_id)))


def _persist_item(batch_id: str, target_id: str, outcome: dict[str, Any]) -> None:
    database.add_batch_item(
        {
            "item_id": uuid4().hex,
            "batch_id": batch_id,
            "target_id": target_id,
            "status": outcome["status"],
            "result_summary": outcome["result_summary"],
            "error_code": outcome.get("error_code"),
            "action_id": None,
        }
    )
    batch = database.get_batch_record(batch_id)
    database.update_batch_record(batch_id, **_counts(batch["items"]))


def _cooperative_call(
    callback: Callable[[], None], batch_id: str, task_id: str
) -> None:
    """Finalize the inner Batch ledger before propagating a safe-point cancel."""
    try:
        callback()
    except Exception:
        now = database.utc_now()
        database.update_batch_record(batch_id, status="skipped", completed_at=now)
        for step in database.get_task_step_records(task_id):
            if step["status"] not in {"success", "cancelled"}:
                database.update_task_step_record(
                    step["step_id"], status="cancelled", completed_at=now,
                    result_summary="批量任务在安全点取消",
                )
        database.update_task_record(
            task_id, status="cancelled", current_step=None, next_action=None,
            updated_at=now, completed_at=now, error_code=None,
        )
        raise


def _counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "success_count": sum(item["status"] == "success" for item in items),
        "failed_count": sum(item["status"] == "failed" for item in items),
        "skipped_count": sum(item["status"] == "skipped" for item in items),
    }


def _final_status(batch: dict[str, Any]) -> str:
    if batch["success_count"] > 0:
        return "success"
    if batch["skipped_count"] > 0:
        return "skipped"
    return "failed"


def _batch_payload(batch: dict[str, Any]) -> dict[str, Any]:
    failures = [
        {
            "target_id": item["target_id"],
            "error_code": item["error_code"],
            "message": item["result_summary"],
        }
        for item in batch["items"] if item["status"] == "failed"
    ]
    return {
        **batch,
        "summary": {
            "total": batch["total"],
            "success": batch["success_count"],
            "failed": batch["failed_count"],
            "skipped": batch["skipped_count"],
        },
        "failure_details": failures,
    }


def _succeeded(summary: str) -> dict[str, Any]:
    return {"status": "success", "result_summary": summary, "error_code": None}


def _failed(code: str, summary: str) -> dict[str, Any]:
    return {"status": "failed", "result_summary": summary, "error_code": code}


def _skipped(summary: str) -> dict[str, Any]:
    return {"status": "skipped", "result_summary": summary, "error_code": None}
