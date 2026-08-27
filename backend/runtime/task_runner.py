"""Durable Task/Step checkpoints around the existing single-Agent loop."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any
from uuid import uuid4

from backend import database
from backend.runtime.planner import NodeType, StepType, TaskPlan
from backend.runtime.workflow_condition import (
    REFERENCE_MISSING,
    evaluate_condition,
    resolve_reference,
)
from backend.runtime.safety_policy import (
    RiskLevel,
    assess_tool_execution,
    classify_tool_risk,
    record_safety_trace,
)
from backend.services.trace_service import record_trace


StepExecutor = Callable[[dict[str, Any]], tuple[dict[str, Any], int]]


class StepStatus(str, Enum):
    """Unified V3 status names with V2-compatible persisted values."""

    CREATED = "created"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    WAITING_CONFIRMATION = "waiting_confirmation"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


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
        "checkpoint_data": {"completed_steps": [], "plan": plan.as_dict()},
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
        "error_code": None,
    }
    steps = [
        {
            "step_id": step.step_id,
            "sequence": step.sequence,
            "step_name": step.step_name,
            "step_type": step.step_type.value,
            "tool_name": step.tool_name,
            "arguments": step.input,
            "status": StepStatus.CREATED.value,
            "retry_count": 0,
        }
        for step in plan.steps
    ]
    database.create_task_record(task, steps)
    return database.get_task_record(task_id)


def start_tool_step(
    *, task_id: str, tool_name: str, arguments: dict[str, Any]
) -> str | None:
    """Claim one planned Tool step and trace it before the Tool is executed."""
    task = database.get_task_record(task_id)
    if task is None:
        raise ValueError("Task 不存在")
    steps = database.get_task_step_records(task_id)
    step = _matching_step(steps, tool_name, arguments)
    if step is None:
        return None
    if not _step_is_ready(step, steps, task["checkpoint_data"]):
        return None
    _mark_step_running(task, step, arguments)
    return str(step["step_id"])


def complete_generation_step(task_id: str, summary: str) -> None:
    """Complete the next synthetic content-generation step with paired traces."""
    task = database.get_task_record(task_id)
    if task is None:
        raise ValueError("Task 不存在")
    step = next(
        (
            item for item in database.get_task_step_records(task_id)
            if item["step_type"] == StepType.GENERATE.value
            and item.get("tool_name") == "generate_content"
            and item["status"] != StepStatus.SUCCESS.value
        ),
        None,
    )
    if step is None:
        return
    steps = database.get_task_step_records(task_id)
    if not _step_is_ready(step, steps, task["checkpoint_data"]):
        return
    if step["status"] != StepStatus.RUNNING.value:
        _mark_step_running(task, step, {"source": "agent_generation"})
    now = database.utc_now()
    database.update_task_step_record(
        step["step_id"], status=StepStatus.SUCCESS.value,
        result_summary=str(summary)[:500], completed_at=now,
    )
    _record_step_trace(
        task=task, step=step, event_type="step_completed",
        result_status=StepStatus.SUCCESS.value,
        result={"ok": True, "message": summary},
    )
    _refresh_checkpoint(task_id)


def start_generation_step(task_id: str) -> str | None:
    """Start generation only when it is the next incomplete planned step."""
    task = database.get_task_record(task_id)
    if task is None:
        raise ValueError("Task 不存在")
    steps = database.get_task_step_records(task_id)
    step = next(
        (
            item for item in steps
            if item["status"] not in {StepStatus.SUCCESS.value, StepStatus.CANCELLED.value}
            and _step_is_ready(item, steps, task["checkpoint_data"])
        ),
        None,
    )
    if (
        step is None
        or step["step_type"] != StepType.GENERATE.value
        or step.get("tool_name") != "generate_content"
    ):
        return None
    if step["status"] != StepStatus.RUNNING.value:
        _mark_step_running(task, step, {"source": "agent_generation"})
    return str(step["step_id"])


def begin_confirmed_action(action_id: str) -> dict[str, Any] | None:
    """Record CONFIRMED and start WRITE before executing a frozen file action."""
    task = database.find_task_waiting_for_action(action_id)
    if task is None:
        return None
    steps = database.get_task_step_records(task["task_id"])
    confirmation = _step_by_type(steps, StepType.CONFIRM)
    write_step = _step_by_type(steps, StepType.WRITE)
    now = database.utc_now()
    if confirmation and confirmation["status"] == StepStatus.WAITING_CONFIRMATION.value:
        database.update_task_step_record(
            confirmation["step_id"], status=StepStatus.CONFIRMED.value,
            completed_at=now, result_summary="用户已确认",
        )
        _record_step_trace(
            task=task, step=confirmation, event_type="step_confirmed",
            result_status=StepStatus.CONFIRMED.value,
            result={"ok": True, "message": "用户已确认"},
        )
    steps = database.get_task_step_records(task["task_id"])
    if write_step and _step_is_ready(write_step, steps, task["checkpoint_data"]) and write_step["status"] not in {
        StepStatus.SUCCESS.value, StepStatus.RUNNING.value
    }:
        _mark_step_running(
            task, write_step, {"action_id": action_id}, update_task=False
        )
    return database.get_task_record(task["task_id"])


def record_tool_execution(
    *,
    task_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
    retry_count: int,
    step_id: str | None = None,
    duration_ms: int = 0,
) -> None:
    steps = database.get_task_step_records(task_id)
    step = (
        next((item for item in steps if item["step_id"] == step_id), None)
        if step_id is not None
        else _matching_step(steps, tool_name, arguments)
    )
    if step is None:
        raise ValueError(f"Task {task_id} 中不存在 Tool Step：{tool_name}")
    task = database.get_task_record(task_id)
    if task is None:
        raise ValueError("Task 不存在")
    if step["status"] not in {StepStatus.RUNNING.value, StepStatus.SUCCESS.value}:
        if not _step_is_ready(step, steps, task["checkpoint_data"]):
            raise ValueError(f"Workflow Node 依赖尚未满足：{step['step_id']}")
        _mark_step_running(task, step, arguments)
    now = database.utc_now()
    ok = result.get("ok") is True
    database.update_task_step_record(
        step["step_id"],
        arguments_json=arguments,
        status=StepStatus.SUCCESS.value if ok else StepStatus.FAILED.value,
        retry_count=retry_count,
        result_summary=_summary(result),
        failed_reason=None if ok else result.get("message"),
        started_at=step.get("started_at") or now,
        completed_at=now,
    )
    try:
        _record_step_trace(
            task=task, step=step, event_type="tool_execution",
            result_status=StepStatus.SUCCESS.value if ok else StepStatus.FAILED.value,
            arguments=arguments, result=result, duration_ms=duration_ms,
            retry_count=retry_count,
            error_code=None if ok else result.get("error_code"),
        )
    except Exception:
        # Observability must never alter the authoritative Tool outcome.
        pass
    _save_node_result(task_id, {**step, "step_id": step["step_id"]}, result)
    _refresh_checkpoint(task_id)


def evaluate_condition_node(
    *, task_id: str, node_id: str, structured_state: dict[str, Any]
) -> bool:
    """Execute a condition node with an allow-listed schema and select its branch."""
    task = database.get_task_record(task_id)
    if task is None:
        raise ValueError("Task 不存在")
    steps = database.get_task_step_records(task_id)
    step = next((item for item in steps if item["step_id"] == node_id), None)
    if step is None or step.get("node_type") != NodeType.CONDITION.value:
        raise ValueError("Condition Node 不存在")
    if not _step_is_ready(step, steps, task["checkpoint_data"]):
        raise ValueError(f"Workflow Node 依赖尚未满足：{node_id}")
    _mark_step_running(task, step, {"source": "structured_state"})
    try:
        selected = evaluate_condition(step.get("condition") or {}, structured_state)
    except ValueError as exc:
        database.update_task_step_record(
            node_id, status=StepStatus.FAILED.value, failed_reason=str(exc),
            completed_at=database.utc_now(),
        )
        _refresh_checkpoint(task_id)
        raise
    database.update_task_step_record(
        node_id, status=StepStatus.SUCCESS.value,
        result_summary=f"condition={str(selected).lower()}",
        completed_at=database.utc_now(),
    )
    checkpoint = database.get_task_record(task_id)["checkpoint_data"]
    node_results = dict(checkpoint.get("node_results") or {})
    node_results[node_id] = {"ok": True, "data": selected, "output_ref": step.get("output_ref")}
    database.update_task_record(
        task_id, checkpoint_data={**checkpoint, "node_results": node_results},
        updated_at=database.utc_now(),
    )
    _record_step_trace(
        task=task, step=step, event_type="condition_evaluated",
        result_status=StepStatus.SUCCESS.value,
        arguments={"operator": (step.get("condition") or {}).get("operator")},
        result={"ok": True, "selected": selected},
    )
    _skip_unselected_branches(task_id, node_id, selected)
    _refresh_checkpoint(task_id)
    return selected


def execute_workflow(
    task_id: str,
    step_executor: StepExecutor,
    *,
    structured_state: dict[str, Any] | None = None,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Run ready nodes in dependency waves; independent nodes execute concurrently."""
    task = database.get_task_record(task_id)
    if task is None:
        return {"ok": False, "error_code": "TASK_NOT_FOUND", "data": None}
    if task["status"] in {"success", "cancelled", "waiting_confirmation"}:
        return {"ok": True, "error_code": None, "data": task}
    database.update_task_record(
        task_id, status="running", completed_at=None, error_code=None,
        updated_at=database.utc_now(),
    )
    state = dict(structured_state or {})
    while True:
        task = database.get_task_record(task_id)
        steps = database.get_task_step_records(task_id)
        ready = [
            step for step in steps
            if step["status"] in {StepStatus.CREATED.value, StepStatus.FAILED.value}
            and _step_is_ready(step, steps, task["checkpoint_data"])
        ]
        unsafe = next(
            (
                step
                for step in ready
                if step.get("node_type") != NodeType.APPROVAL.value
                and (
                    step.get("step_type") == StepType.WRITE.value
                    or classify_tool_risk(
                        str(step.get("tool_name") or ""), read_only=True
                    )
                    == RiskLevel.HIGH
                )
            ),
            None,
        )
        if unsafe is not None:
            return _reject_unapproved_workflow_node(task, unsafe)
        conditions = [step for step in ready if step.get("node_type") == NodeType.CONDITION.value]
        for step in conditions:
            evaluate_condition_node(
                task_id=task_id, node_id=step["step_id"],
                structured_state=_condition_context(task_id, state),
            )
        if conditions:
            continue
        approval = next(
            (step for step in ready if step.get("node_type") == NodeType.APPROVAL.value), None
        )
        if approval is not None:
            _mark_step_running(task, approval, {"action": "await_user_decision"})
            database.update_task_step_record(
                approval["step_id"], status=StepStatus.WAITING_CONFIRMATION.value
            )
            database.update_task_record(
                task_id, status="waiting_confirmation", current_step=approval["sequence"],
                next_action="等待用户确认", updated_at=database.utc_now(),
            )
            return {"ok": True, "error_code": None, "data": database.get_task_record(task_id)}
        executable = [
            step for step in ready
            if step["step_type"] in {StepType.QUERY.value, StepType.ANALYSIS.value}
        ]
        if not executable:
            break
        for step in executable:
            arguments = _resolved_arguments(step, _condition_context(task_id, state))
            _mark_step_running(task, step, arguments, update_task=False)
            step["arguments"] = arguments
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(executable)))) as pool:
            futures = [(step, pool.submit(_invoke_step_executor, step_executor, step)) for step in executable]
            outcomes = [(step, future.result()) for step, future in futures]
        failed = None
        for step, (result, retry_count) in outcomes:
            record_tool_execution(
                task_id=task_id, step_id=step["step_id"],
                tool_name=step.get("tool_name") or "workflow_node",
                arguments=step["arguments"], result=result, retry_count=retry_count,
            )
            if result.get("ok") is not True and failed is None:
                failed = (step, result)
        if failed is not None:
            step, result = failed
            database.update_task_record(
                task_id, status="failed", next_action=step["step_name"],
                updated_at=database.utc_now(),
                error_code=result.get("error_code") or "STEP_FAILED",
            )
            return {"ok": False, "error_code": result.get("error_code"), "data": database.get_task_record(task_id)}
    steps = database.get_task_step_records(task_id)
    unfinished = [
        step for step in steps
        if step["status"] not in {StepStatus.SUCCESS.value, StepStatus.CANCELLED.value}
    ]
    if unfinished:
        return {"ok": True, "error_code": None, "data": database.get_task_record(task_id)}
    now = database.utc_now()
    database.update_task_record(
        task_id, status="success", current_step=None, next_action=None,
        updated_at=now, completed_at=now, error_code=None,
    )
    return {"ok": True, "error_code": None, "data": database.get_task_record(task_id)}


def _reject_unapproved_workflow_node(
    task: dict[str, Any], step: dict[str, Any]
) -> dict[str, Any]:
    """Fail closed before a Workflow executor can see a HIGH-risk node."""
    arguments = dict(step.get("arguments") or {})
    assessment = assess_tool_execution(
        tool_name=str(step.get("tool_name") or ""),
        arguments=arguments,
        read_only=False,
        requires_confirmation=True,
        confirmation_granted=False,
    )
    now = database.utc_now()
    database.update_task_step_record(
        step["step_id"],
        status=StepStatus.FAILED.value,
        failed_reason="高风险 Workflow Node 缺少有效 Approval",
        completed_at=now,
    )
    database.update_task_record(
        task["task_id"],
        status="failed",
        current_step=step["sequence"],
        next_action="需要创建并完成真实 Approval",
        updated_at=now,
        completed_at=now,
        error_code="CONFIRMATION_REQUIRED",
    )
    try:
        record_safety_trace(
            assessment,
            session_id=task["session_id"],
            task_id=task["task_id"],
            step_id=step["step_id"],
            tool_name=step.get("tool_name"),
            approval_result="missing",
        )
    except Exception:
        pass
    return {
        "ok": False,
        "error_code": "CONFIRMATION_REQUIRED",
        "data": database.get_task_record(task["task_id"]),
    }


def finalize_task(task_id: str, result: dict[str, Any]) -> dict[str, Any]:
    task = database.get_task_record(task_id)
    if task is None:
        raise ValueError("Task 不存在")
    steps = database.get_task_step_records(task_id)
    now = database.utc_now()
    if result.get("status") == "confirmation_required":
        complete_generation_step(task_id, "待确认内容已冻结")
        steps = database.get_task_step_records(task_id)
        confirmation = _step_by_type(steps, StepType.CONFIRM)
        if confirmation:
            if confirmation["status"] == StepStatus.CREATED.value:
                _mark_step_running(task, confirmation, {"action": "await_user_decision"})
            database.update_task_step_record(
                confirmation["step_id"], status=StepStatus.WAITING_CONFIRMATION.value,
                started_at=confirmation.get("started_at") or now,
            )
            _record_step_trace(
                task=task, step=confirmation, event_type="step_waiting_confirmation",
                result_status=StepStatus.WAITING_CONFIRMATION.value,
                result={"ok": True, "message": "等待用户确认"},
            )
        action = result.get("pending_action") or {}
        checkpoint = task["checkpoint_data"]
        checkpoint.update(
            {
                "action_id": action.get("action_id"),
                "approval_binding": {
                    "approval_id": action.get("approval_id")
                    or action.get("action_id"),
                    "operation": action.get("action_type"),
                    "target": {
                        "file_id": action.get("file_id"),
                        "file_name": action.get("target_file"),
                    },
                    "frozen_operation": action.get("operation"),
                },
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
        complete_generation_step(task_id, "最终回答已生成")
        database.update_task_record(
            task_id,
            status="success",
            current_step=None,
            next_action=None,
            checkpoint_data={
                **task["checkpoint_data"],
                "completed_steps": _successful_sequences(task_id),
            },
            updated_at=now,
            completed_at=now,
            error_code=None,
        )
    else:
        database.update_task_record(
            task_id,
            status="failed",
            next_action="可从失败 Step 恢复",
            checkpoint_data={
                **task["checkpoint_data"],
                "completed_steps": _successful_sequences(task_id),
            },
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
    confirmation = _step_by_type(steps, StepType.CONFIRM)
    side_effect = _step_by_type(steps, StepType.WRITE)
    now = database.utc_now()
    if action_status == "cancelled":
        for step in (confirmation, side_effect):
            if step:
                database.update_task_step_record(
                    step["step_id"], status=StepStatus.CANCELLED.value, completed_at=now
                )
                _record_step_trace(
                    task=task, step=step, event_type="step_cancelled",
                    result_status=StepStatus.CANCELLED.value,
                    result={"ok": True, "message": "用户取消操作"},
                )
        database.update_task_record(
            task["task_id"], status="cancelled", next_action=None,
            updated_at=now, completed_at=now, error_code=None,
        )
    elif success:
        if confirmation:
            database.update_task_step_record(
                confirmation["step_id"], status=StepStatus.SUCCESS.value, completed_at=now,
                result_summary="用户已确认",
            )
        if side_effect:
            database.update_task_step_record(
                side_effect["step_id"], status=StepStatus.SUCCESS.value, retry_count=0,
                started_at=now, completed_at=now,
                result_summary="幂等 action 已执行；未自动重试",
            )
            _record_step_trace(
                task=task, step=side_effect, event_type="tool_execution",
                result_status=StepStatus.SUCCESS.value,
                arguments={"action_id": action_id},
                result={"ok": True, "message": "幂等 action 已执行"},
            )
        database.update_task_record(
            task["task_id"], status="success", current_step=None, next_action=None,
            checkpoint_data={
                **task["checkpoint_data"], "action_id": action_id,
                "completed_steps": _successful_sequences(task["task_id"]),
            },
            updated_at=now, completed_at=now, error_code=None,
        )
    else:
        if confirmation:
            database.update_task_step_record(
                confirmation["step_id"], status=StepStatus.SUCCESS.value, completed_at=now,
                result_summary="用户已确认；执行动作失败",
            )
        if side_effect:
            database.update_task_step_record(
                side_effect["step_id"], status=StepStatus.FAILED.value, retry_count=0,
                started_at=now, completed_at=now,
                failed_reason="确认动作执行失败；副作用 Step 禁止自动重试",
            )
            _record_step_trace(
                task=task, step=side_effect, event_type="tool_execution",
                result_status=StepStatus.FAILED.value,
                arguments={"action_id": action_id},
                result={"ok": False, "message": "确认动作执行失败"},
                error_code="ACTION_EXECUTION_FAILED",
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
    _invalidate_changed_file_inputs(task_id)
    return execute_workflow(task_id, step_executor)


def _matching_step(
    steps: list[dict[str, Any]], tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    candidates = [
        step for step in steps
        if step["tool_name"] == tool_name
        and step["status"] not in {StepStatus.SUCCESS.value, StepStatus.CANCELLED.value}
    ]
    if tool_name == "query_table" and len(candidates) > 1:
        selected = {str(item) for item in arguments.get("select") or []}
        wanted = "查询科研成果" if selected.intersection({"论文数", "专利数", "竞赛数"}) else "查询学生成绩"
        return next((step for step in candidates if step["step_name"] == wanted), candidates[0])
    return candidates[0] if candidates else None


def _step_is_ready(
    step: dict[str, Any],
    steps: list[dict[str, Any]],
    checkpoint: dict[str, Any],
) -> bool:
    """Return true only when dependencies and an optional branch selection permit execution."""
    statuses = {item["step_id"]: item for item in steps}
    for dependency_id in step.get("depends_on") or []:
        dependency = statuses.get(dependency_id)
        if dependency is None:
            return False
        if dependency["status"] not in {StepStatus.SUCCESS.value, StepStatus.CONFIRMED.value}:
            if not (
                dependency["status"] == StepStatus.CANCELLED.value
                and dependency.get("failed_reason") == "condition_not_selected"
            ):
                return False
    node_results = checkpoint.get("node_results") or {}
    for condition_id, expected in (step.get("run_if") or {}).items():
        result = node_results.get(condition_id)
        if not isinstance(result, dict) or result.get("data") is not bool(expected):
            return False
    return True


def _skip_unselected_branches(task_id: str, condition_id: str, selected: bool) -> None:
    now = database.utc_now()
    for step in database.get_task_step_records(task_id):
        expected = (step.get("run_if") or {}).get(condition_id)
        if expected is not None and bool(expected) is not selected and step["status"] == StepStatus.CREATED.value:
            database.update_task_step_record(
                step["step_id"], status=StepStatus.CANCELLED.value,
                failed_reason="condition_not_selected", completed_at=now,
                result_summary="条件分支未选中",
            )


def _condition_context(task_id: str, state: dict[str, Any]) -> dict[str, Any]:
    task = database.get_task_record(task_id)
    return {
        **state,
        "state": state,
        "nodes": dict((task["checkpoint_data"].get("node_results") or {})),
    }


def _resolved_arguments(step: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    arguments = dict(step.get("arguments") or {})
    for target, reference in (step.get("input_ref") or {}).items():
        value = resolve_reference(context, reference)
        # Missing references are rejected by dependency/condition design and are not injected.
        if value is not REFERENCE_MISSING:
            arguments[target] = value
    return arguments


def _invoke_step_executor(
    step_executor: StepExecutor, step: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    try:
        return step_executor(step)
    except Exception as exc:
        return ({
            "ok": False,
            "data": None,
            "error_code": "WORKFLOW_NODE_EXECUTION_ERROR",
            "message": str(exc)[:500] or "Workflow Node 执行失败",
        }, 0)


def _save_node_result(
    task_id: str, step: dict[str, Any], result: dict[str, Any]
) -> None:
    task = database.get_task_record(task_id)
    if task is None:
        return
    checkpoint = task["checkpoint_data"]
    node_results = dict(checkpoint.get("node_results") or {})
    node_results[step["step_id"]] = {
        "ok": result.get("ok") is True,
        "data": result.get("data"),
        "error_code": result.get("error_code"),
        "message": str(result.get("message") or "")[:500],
        "output_ref": step.get("output_ref"),
        "file_fingerprints": _file_fingerprints(step.get("arguments") or {}),
    }
    database.update_task_record(
        task_id, checkpoint_data={**checkpoint, "node_results": node_results},
        updated_at=database.utc_now(),
    )


def _file_fingerprints(arguments: dict[str, Any]) -> dict[str, str]:
    file_ids: set[str] = set()

    def collect(value: Any, key: str | None = None) -> None:
        if key == "file_id" and isinstance(value, str) and value:
            file_ids.add(value)
        elif key == "file_ids" and isinstance(value, list):
            file_ids.update(item for item in value if isinstance(item, str) and item)
        elif isinstance(value, dict):
            for child_key, child in value.items():
                collect(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(arguments)
    fingerprints = {}
    for file_id in file_ids:
        version = database.get_latest_file_version(file_id)
        if version is not None:
            fingerprints[file_id] = str(version.get("version_id") or version.get("content_hash"))
            continue
        record = database.get_file_record_by_id(file_id)
        if record is not None:
            fingerprints[file_id] = str(record.get("created_at") or record.get("file_path") or file_id)
    return fingerprints


def _invalidate_changed_file_inputs(task_id: str) -> None:
    """Invalidate read nodes whose referenced file version changed, never replay writes."""
    task = database.get_task_record(task_id)
    if task is None:
        return
    steps = database.get_task_step_records(task_id)
    results = dict(task["checkpoint_data"].get("node_results") or {})
    invalid: set[str] = set()
    for step in steps:
        saved = results.get(step["step_id"]) or {}
        fingerprints = saved.get("file_fingerprints") or {}
        if (
            step["status"] == StepStatus.SUCCESS.value
            and step["step_type"] != StepType.WRITE.value
            and fingerprints
            and _file_fingerprints(step.get("arguments") or {}) != fingerprints
        ):
            invalid.add(step["step_id"])
    changed = True
    while changed:
        changed = False
        for step in steps:
            if (
                step["step_id"] not in invalid
                and step["step_type"] != StepType.WRITE.value
                and set(step.get("depends_on") or []).intersection(invalid)
            ):
                invalid.add(step["step_id"])
                changed = True
    for step in steps:
        if step["step_id"] in invalid:
            database.update_task_step_record(
                step["step_id"], status=StepStatus.CREATED.value, retry_count=0,
                result_summary=None, failed_reason=None, started_at=None, completed_at=None,
            )
            results.pop(step["step_id"], None)
    if invalid:
        checkpoint = task["checkpoint_data"]
        database.update_task_record(
            task_id,
            checkpoint_data={**checkpoint, "node_results": results,
                             "invalidated_nodes": sorted(invalid)},
            updated_at=database.utc_now(),
        )


def _refresh_checkpoint(task_id: str) -> None:
    task = database.get_task_record(task_id)
    steps = database.get_task_step_records(task_id)
    pending = next(
        (
            step for step in steps
            if step["status"] not in {StepStatus.SUCCESS.value, StepStatus.CANCELLED.value}
        ),
        None,
    )
    database.update_task_record(
        task_id,
        current_step=pending["sequence"] if pending else None,
        next_action=pending["step_name"] if pending else None,
        checkpoint_data={**task["checkpoint_data"], "completed_steps": _successful_sequences(task_id)},
        updated_at=database.utc_now(),
    )


def _successful_sequences(task_id: str) -> list[int]:
    return [
        step["sequence"] for step in database.get_task_step_records(task_id)
        if step["status"] == StepStatus.SUCCESS.value
    ]


def _summary(result: dict[str, Any]) -> str:
    summary = result.get("result_summary") or result.get("message") or "Tool 已执行"
    return str(summary)[:500]


def _step_by_type(
    steps: list[dict[str, Any]], step_type: StepType
) -> dict[str, Any] | None:
    return next((step for step in steps if step["step_type"] == step_type.value), None)


def _mark_step_running(
    task: dict[str, Any],
    step: dict[str, Any],
    arguments: dict[str, Any],
    *,
    update_task: bool = True,
) -> None:
    """Persist RUNNING and its trace before any authoritative execution."""
    now = database.utc_now()
    database.update_task_step_record(
        step["step_id"], arguments_json=arguments,
        status=StepStatus.RUNNING.value,
        started_at=step.get("started_at") or now,
        completed_at=None, failed_reason=None,
    )
    if update_task:
        database.update_task_record(
            task["task_id"], status="running", current_step=step["sequence"],
            next_action=step["step_name"], updated_at=now,
        )
    _record_step_trace(
        task=task, step=step, event_type="step_started",
        result_status=StepStatus.RUNNING.value, arguments=arguments,
        result={"status": StepStatus.RUNNING.value, "message": "Step 开始执行"},
    )


def _record_step_trace(
    *,
    task: dict[str, Any],
    step: dict[str, Any],
    event_type: str,
    result_status: str,
    arguments: Any = None,
    result: Any = None,
    duration_ms: int = 0,
    retry_count: int = 0,
    error_code: str | None = None,
) -> None:
    try:
        record_trace(
            task_id=task["task_id"], session_id=task["session_id"],
            step_id=step["step_id"], event_type=event_type,
            tool_name=step.get("tool_name"), arguments=arguments, result=result,
            duration_ms=duration_ms, retry_count=retry_count,
            result_status=result_status, error_code=error_code,
            metrics={
                "workflow_id": step.get("workflow_id"),
                "node_id": step.get("node_id") or step.get("step_id"),
                "node_type": step.get("node_type") or "tool",
                "step_type": step.get("step_type"),
            },
        )
    except Exception:
        # Trace remains non-authoritative and must not alter Task execution.
        pass
