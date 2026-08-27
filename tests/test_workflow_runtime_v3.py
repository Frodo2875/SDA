"""V3.15 WF01-WF07 dependency, parallel, condition, approval and resume tests."""

import threading

import pytest

from backend import database
from backend.runtime.planner import NodeType, PlannedStep, StepType, TaskPlan
from backend.runtime.policy import MAX_RETRIES
from backend.runtime.task_runner import (
    StepStatus,
    execute_workflow,
    finalize_task,
    record_tool_execution,
    resume_after_action,
    resume_task,
    start_task,
    start_tool_step,
)
from backend.runtime.tool_executor import execute_with_retry
from backend.runtime.workflow_condition import evaluate_condition
from backend.runtime import task_runner


def success(value: object = 1) -> dict:
    return {"ok": True, "data": {"value": value}, "error_code": None, "message": "成功"}


def test_wf01_sequential_dependency_gating() -> None:
    a, b, c = "wf01-a", "wf01-b", "wf01-c"
    plan = TaskPlan("sequential", (
        PlannedStep(1, "A", StepType.QUERY, "tool_a", step_id=a),
        PlannedStep(2, "B", StepType.QUERY, "tool_b", step_id=b, depends_on=(a,)),
        PlannedStep(3, "C", StepType.QUERY, "tool_c", step_id=c, depends_on=(b,)),
    ))
    task = start_task(session_id="wf01", user_message="A B C", plan=plan)
    first = database.get_task_step_records(task["task_id"])[0]
    assert set(first) >= {
        "node_id", "workflow_id", "node_type", "depends_on", "status",
        "tool_name", "input_ref", "output_ref", "retry_count", "failed_reason",
    }
    assert start_tool_step(task_id=task["task_id"], tool_name="tool_b", arguments={}) is None
    a_id = start_tool_step(task_id=task["task_id"], tool_name="tool_a", arguments={})
    assert a_id == a
    record_tool_execution(task_id=task["task_id"], step_id=a, tool_name="tool_a", arguments={}, result=success(), retry_count=0)
    assert start_tool_step(task_id=task["task_id"], tool_name="tool_b", arguments={}) == b
    assert start_tool_step(task_id=task["task_id"], tool_name="tool_c", arguments={}) is None


def test_wf02_parallel_nodes_overlap_and_aggregate_waits() -> None:
    identity, scores, research, aggregate = "wf02-id", "wf02-score", "wf02-research", "wf02-aggregate"
    plan = TaskPlan("parallel", (
        PlannedStep(1, "确认学生", StepType.QUERY, "identity", step_id=identity),
        PlannedStep(2, "成绩查询", StepType.QUERY, "scores", step_id=scores, depends_on=(identity,)),
        PlannedStep(3, "科研查询", StepType.QUERY, "research", step_id=research, depends_on=(identity,)),
        PlannedStep(4, "汇总", StepType.ANALYSIS, "aggregate", step_id=aggregate,
                    depends_on=(scores, research), node_type=NodeType.AGGREGATE),
    ))
    task = start_task(session_id="wf02", user_message="并行查询", plan=plan)
    barrier = threading.Barrier(2, timeout=2)
    overlapped: list[str] = []

    def executor(step):
        if step["tool_name"] in {"scores", "research"}:
            overlapped.append(step["tool_name"])
            barrier.wait()
        if step["tool_name"] == "aggregate":
            statuses = {item["step_id"]: item["status"] for item in database.get_task_step_records(task["task_id"])}
            assert statuses[scores] == statuses[research] == StepStatus.SUCCESS.value
        return success(step["tool_name"]), 0

    result = execute_workflow(task["task_id"], executor, max_workers=2)
    assert result["data"]["status"] == "success"
    assert set(overlapped) == {"scores", "research"}


@pytest.mark.parametrize(
    ("missing", "expected_tool", "skipped_tool"),
    [(2, "report_missing", "eligibility_check"), (0, "eligibility_check", "report_missing")],
    ids=["WF03-missing-branch", "WF04-eligibility-branch"],
)
def test_wf03_wf04_safe_conditional_branches(
    missing: int, expected_tool: str, skipped_tool: str
) -> None:
    condition_id = f"condition-{missing}"
    plan = TaskPlan("conditional", (
        PlannedStep(
            1, "材料完整性判断", StepType.ANALYSIS, None, step_id=condition_id,
            node_type=NodeType.CONDITION,
            condition={"ref": "state.missing_required_materials", "operator": "gt", "value": 0},
            output_ref="has_missing",
        ),
        PlannedStep(2, "报告缺失", StepType.ANALYSIS, "report_missing",
                    run_if={condition_id: True}),
        PlannedStep(3, "资格判断", StepType.ANALYSIS, "eligibility_check",
                    run_if={condition_id: False}),
    ))
    task = start_task(session_id=f"wf-condition-{missing}", user_message="条件分支", plan=plan)
    called: list[str] = []
    result = execute_workflow(
        task["task_id"], lambda step: (called.append(step["tool_name"]) or success(), 0),
        structured_state={"missing_required_materials": missing},
    )
    assert result["data"]["status"] == "success"
    assert called == [expected_tool]
    statuses = {item["tool_name"]: item for item in database.get_task_step_records(task["task_id"])}
    assert statuses[skipped_tool]["status"] == StepStatus.CANCELLED.value
    assert statuses[skipped_tool]["failed_reason"] == "condition_not_selected"
    with pytest.raises(ValueError):
        evaluate_condition({"ref": "state.x", "operator": "eval", "value": "x"}, {"state": {"x": 1}})


def test_wf05_approval_blocks_write_and_cancel_preserves_it() -> None:
    query, approval, write = "wf05-query", "wf05-approval", "wf05-write"
    plan = TaskPlan("approval", (
        PlannedStep(1, "读取", StepType.QUERY, "read", step_id=query),
        PlannedStep(2, "审批", StepType.CONFIRM, "confirm_action", step_id=approval,
                    node_type=NodeType.APPROVAL, depends_on=(query,)),
        PlannedStep(3, "写入", StepType.WRITE, "write", step_id=write, depends_on=(approval,)),
    ))
    task = start_task(session_id="wf05", user_message="审批写入", plan=plan)
    record_tool_execution(task_id=task["task_id"], step_id=query, tool_name="read",
                          arguments={}, result=success(), retry_count=0)
    action_id = "wf05-action"
    finalized = finalize_task(task["task_id"], {
        "status": "confirmation_required", "pending_action": {"action_id": action_id}
    })
    assert finalized["status"] == "waiting_confirmation"
    steps = {item["step_id"]: item for item in database.get_task_step_records(task["task_id"])}
    assert steps[approval]["status"] == StepStatus.WAITING_CONFIRMATION.value
    assert steps[write]["status"] == StepStatus.CREATED.value
    resume_after_action(action_id, "cancelled", success=False)
    steps = {item["step_id"]: item for item in database.get_task_step_records(task["task_id"])}
    assert steps[write]["status"] == StepStatus.CANCELLED.value


def test_wf06_retry_is_bounded_and_persisted_failed() -> None:
    plan = TaskPlan("bounded_retry", (PlannedStep(1, "读取", StepType.QUERY, "query_table"),))
    task = start_task(session_id="wf06", user_message="有限重试", plan=plan)
    attempts = 0

    def executor(step):
        nonlocal attempts

        def fail_once(raw):
            nonlocal attempts
            attempts += 1
            return {}, {"ok": False, "data": None, "error_code": "TABLE_READ_ERROR", "message": "失败"}

        _, result, retries = execute_with_retry(
            tool_name="query_table", raw_arguments={}, execute_once=fail_once,
            retryable=True, read_only=True, requires_confirmation=False, max_retries=999,
        )
        return result, retries

    result = execute_workflow(task["task_id"], executor)
    step = database.get_task_step_records(task["task_id"])[0]
    assert result["ok"] is False
    assert attempts == MAX_RETRIES + 1
    assert step["retry_count"] == MAX_RETRIES
    assert step["status"] == StepStatus.FAILED.value


def test_wf07_resume_skips_successful_nodes_and_never_replays_write() -> None:
    write, remaining = "wf07-write", "wf07-remaining"
    plan = TaskPlan("resume", (
        PlannedStep(1, "已确认写入", StepType.WRITE, "write_word", step_id=write),
        PlannedStep(2, "剩余只读", StepType.QUERY, "read_after", step_id=remaining, depends_on=(write,)),
    ))
    task = start_task(session_id="wf07", user_message="恢复", plan=plan)
    record_tool_execution(task_id=task["task_id"], step_id=write, tool_name="write_word",
                          arguments={"action_id": "already-executed"}, result=success(), retry_count=0)
    database.update_task_record(task["task_id"], status="failed", updated_at=database.utc_now(), error_code="INTERRUPTED")
    called: list[str] = []
    result = resume_task(
        task["task_id"], lambda step: (called.append(step["tool_name"]) or success(), 0)
    )
    assert result["data"]["status"] == "success"
    assert called == ["read_after"]
    assert database.get_task_step_records(task["task_id"])[0]["status"] == StepStatus.SUCCESS.value


def test_wf07_changed_file_version_invalidates_related_read_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, dependent = "wf07-source", "wf07-dependent"
    plan = TaskPlan("version_resume", (
        PlannedStep(1, "文件读取", StepType.QUERY, "read_file", step_id=source,
                    input={"file_id": "file-1"}),
        PlannedStep(2, "相关分析", StepType.ANALYSIS, "analyse", step_id=dependent,
                    depends_on=(source,)),
    ))
    current = {"file-1": "version-1"}
    monkeypatch.setattr(task_runner, "_file_fingerprints", lambda arguments: dict(current))
    task = start_task(session_id="wf07-version", user_message="文件变更恢复", plan=plan)
    record_tool_execution(
        task_id=task["task_id"], step_id=source, tool_name="read_file",
        arguments={"file_id": "file-1"}, result=success(), retry_count=0,
    )
    database.update_task_record(
        task["task_id"], status="failed", updated_at=database.utc_now(), error_code="INTERRUPTED"
    )
    current["file-1"] = "version-2"
    called: list[str] = []
    result = resume_task(
        task["task_id"], lambda step: (called.append(step["tool_name"]) or success(), 0)
    )
    assert result["data"]["status"] == "success"
    assert called == ["read_file", "analyse"]
