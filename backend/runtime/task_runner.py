"""Durable Task/Step checkpoints around the existing single-Agent loop."""

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from backend import database
from backend.runtime.planner import TaskPlan


StepExecutor = Callable[[dict[str, Any]], tuple[dict[str, Any], int]]


def start_task(
    *, session_id: str, user_message: str, plan: TaskPlan
) -> dict[str, Any]:
    now = database.utc_now()
    task_id = uuid4().hex
    task = {
        "task_id": task_id,
        "session_id": session_id,
        "user_message": user_message,
        "task_type": plan.task_type,
        "status": "running",
        "current_step": 1,
        "next_action": plan.steps[0].step_name,
        "checkpoint_data": {"completed_steps": []},
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
        "error_code": None,
    }
    steps = [
        {
            "step_id": uuid4().hex,
            "sequence": step.sequence,
            "step_name": step.step_name,
            "step_type": step.step_type,
            "tool_name": step.tool_name,
            "arguments": {},
            "status": "pending",
            "retry_count": 0,
        }
        for step in plan.steps
    ]
    database.create_task_record(task, steps)
    return database.get_task_record(task_id)


def record_tool_execution(
    *,
    task_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
    retry_count: int,
    step_id: str | None = None,
) -> None:
    steps = database.get_task_step_records(task_id)
    step = (
        next((item for item in steps if item["step_id"] == step_id), None)
        if step_id is not None
        else _matching_step(steps, tool_name, arguments)
    )
    if step is None:
        return
    now = database.utc_now()
    ok = result.get("ok") is True
    database.update_task_step_record(
        step["step_id"],
        arguments_json=arguments,
        status="success" if ok else "failed",
        retry_count=retry_count,
        result_summary=_summary(result),
        failed_reason=None if ok else result.get("message"),
        started_at=step.get("started_at") or now,
        completed_at=now,
    )
    _refresh_checkpoint(task_id)


def finalize_task(task_id: str, result: dict[str, Any]) -> dict[str, Any]:
    task = database.get_task_record(task_id)
    if task is None:
        raise ValueError("Task 不存在")
    steps = database.get_task_step_records(task_id)
    now = database.utc_now()
    if result.get("status") == "confirmation_required":
        _complete_non_tool_step(steps, "generation", "待确认内容已冻结")
        confirmation = next((step for step in steps if step["step_type"] == "confirmation"), None)
        if confirmation:
            database.update_task_step_record(
                confirmation["step_id"], status="waiting_confirmation", started_at=now
            )
        action = result.get("pending_action") or {}
        checkpoint = task["checkpoint_data"]
        checkpoint.update(
            {
                "action_id": action.get("action_id"),
                "completed_steps": _successful_sequences(task_id),
            }
        )
        database.update_task_record(
            task_id,
            status="waiting_confirmation",
            current_step=confirmation["sequence"] if confirmation else None,
            next_action="等待用户确认",
            checkpoint_data=checkpoint,
            updated_at=now,
            error_code=None,
        )
    elif result.get("status") == "completed":
        _complete_non_tool_step(steps, "generation", "最终回答已生成")
        database.update_task_record(
            task_id,
            status="success",
            current_step=None,
            next_action=None,
            checkpoint_data={"completed_steps": _successful_sequences(task_id)},
            updated_at=now,
            completed_at=now,
            error_code=None,
        )
    else:
        database.update_task_record(
            task_id,
            status="failed",
            next_action="可从失败 Step 恢复",
            checkpoint_data={"completed_steps": _successful_sequences(task_id)},
            updated_at=now,
            completed_at=now,
            error_code=str(result.get("status") or "TASK_FAILED"),
        )
    return database.get_task_record(task_id)


def resume_after_action(action_id: str, action_status: str, *, success: bool) -> dict[str, Any] | None:
    """Advance only confirmation/write steps; prior successful reads are untouched."""
    task = database.find_task_waiting_for_action(action_id)
    if task is None:
        return None
    steps = database.get_task_step_records(task["task_id"])
    confirmation = next((step for step in steps if step["step_type"] == "confirmation"), None)
    side_effect = next((step for step in steps if step["step_type"] == "side_effect"), None)
    now = database.utc_now()
    if action_status == "cancelled":
        for step in (confirmation, side_effect):
            if step:
                database.update_task_step_record(
                    step["step_id"], status="cancelled", completed_at=now
                )
        database.update_task_record(
            task["task_id"], status="cancelled", next_action=None,
            updated_at=now, completed_at=now, error_code=None,
        )
    elif success:
        if confirmation:
            database.update_task_step_record(
                confirmation["step_id"], status="success", completed_at=now,
                result_summary="用户已确认",
            )
        if side_effect:
            database.update_task_step_record(
                side_effect["step_id"], status="success", retry_count=0,
                started_at=now, completed_at=now,
                result_summary="幂等 action 已执行；未自动重试",
            )
        database.update_task_record(
            task["task_id"], status="success", current_step=None, next_action=None,
            checkpoint_data={"action_id": action_id, "completed_steps": _successful_sequences(task["task_id"])},
            updated_at=now, completed_at=now, error_code=None,
        )
    else:
        if confirmation:
            database.update_task_step_record(
                confirmation["step_id"], status="success", completed_at=now,
                result_summary="用户已确认；执行动作失败",
            )
        if side_effect:
            database.update_task_step_record(
                side_effect["step_id"], status="failed", retry_count=0,
                started_at=now, completed_at=now,
                failed_reason="确认动作执行失败；副作用 Step 禁止自动重试",
            )
        database.update_task_record(
            task["task_id"], status="failed", next_action="需要人工处理失败的确认动作",
            updated_at=now, completed_at=now, error_code="ACTION_EXECUTION_FAILED",
        )
    return database.get_task_record(task["task_id"])


def resume_task(
    task_id: str, step_executor: StepExecutor | None = None
) -> dict[str, Any]:
    """Resume pending/failed steps and never execute an already successful Step."""
    task = database.get_task_record(task_id)
    if task is None:
        return {"ok": False, "error_code": "TASK_NOT_FOUND", "data": None}
    if task["status"] == "waiting_confirmation":
        action_id = task["checkpoint_data"].get("action_id")
        action = database.get_pending_action_record(action_id) if action_id else None
        if action and action["status"] in {"executed", "cancelled", "failed"}:
            updated = resume_after_action(
                action_id, action["status"], success=action["status"] == "executed"
            )
            return {"ok": True, "error_code": None, "data": updated}
        return {"ok": True, "error_code": None, "data": task}
    if step_executor is None or task["status"] in {"success", "cancelled"}:
        return {"ok": True, "error_code": None, "data": task}

    database.update_task_record(
        task_id, status="running", updated_at=database.utc_now(), completed_at=None,
        error_code=None,
    )
    for step in database.get_task_step_records(task_id):
        if step["status"] == "success" or step["step_type"] != "tool":
            continue
        result, retry_count = step_executor(step)
        record_tool_execution(
            task_id=task_id,
            tool_name=step["tool_name"],
            arguments=step["arguments"],
            result=result,
            retry_count=retry_count,
            step_id=step["step_id"],
        )
        if result.get("ok") is not True:
            database.update_task_record(
                task_id, status="failed", next_action=step["step_name"],
                updated_at=database.utc_now(), error_code=result.get("error_code") or "STEP_FAILED",
            )
            return {"ok": False, "error_code": result.get("error_code"), "data": database.get_task_record(task_id)}
    database.update_task_record(
        task_id, status="success", current_step=None, next_action=None,
        checkpoint_data={"completed_steps": _successful_sequences(task_id)},
        updated_at=database.utc_now(), completed_at=database.utc_now(), error_code=None,
    )
    return {"ok": True, "error_code": None, "data": database.get_task_record(task_id)}


def _matching_step(
    steps: list[dict[str, Any]], tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    candidates = [
        step for step in steps
        if step["tool_name"] == tool_name and step["status"] != "success"
    ]
    if tool_name == "query_table" and len(candidates) > 1:
        selected = {str(item) for item in arguments.get("select") or []}
        wanted = "查询科研成果" if selected.intersection({"论文数", "专利数", "竞赛数"}) else "查询学生成绩"
        return next((step for step in candidates if step["step_name"] == wanted), candidates[0])
    return candidates[0] if candidates else None


def _refresh_checkpoint(task_id: str) -> None:
    task = database.get_task_record(task_id)
    steps = database.get_task_step_records(task_id)
    pending = next((step for step in steps if step["status"] != "success"), None)
    database.update_task_record(
        task_id,
        current_step=pending["sequence"] if pending else None,
        next_action=pending["step_name"] if pending else None,
        checkpoint_data={**task["checkpoint_data"], "completed_steps": _successful_sequences(task_id)},
        updated_at=database.utc_now(),
    )


def _complete_non_tool_step(steps: list[dict[str, Any]], step_type: str, summary: str) -> None:
    step = next((item for item in steps if item["step_type"] == step_type and item["status"] != "success"), None)
    if step:
        now = database.utc_now()
        database.update_task_step_record(
            step["step_id"], status="success", started_at=now, completed_at=now,
            result_summary=summary,
        )


def _successful_sequences(task_id: str) -> list[int]:
    return [
        step["sequence"] for step in database.get_task_step_records(task_id)
        if step["status"] == "success"
    ]


def _summary(result: dict[str, Any]) -> str:
    summary = result.get("result_summary") or result.get("message") or "Tool 已执行"
    return str(summary)[:500]
