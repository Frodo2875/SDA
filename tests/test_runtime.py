"""V2.8 P01-P06 lightweight Planning, Retry, Task, and Resume tests."""

import json
from typing import Any

import pytest

from backend import database
from backend.agent import run_agent
from backend.runtime.planner import TaskPlan, PlannedStep, create_plan
from backend.runtime.policy import MAX_RETRIES
from backend.runtime.task_runner import (
    record_tool_execution,
    resume_task,
    start_task,
)
from backend.runtime.tool_executor import execute_with_retry
from backend.services import confirmation


class FinalAnswerClient:
    async def create_chat_completion(self, messages, tools):
        return {"role": "assistant", "content": "S001 平均成绩查询完成。", "tool_calls": []}


class WriteRequestClient:
    def __init__(self) -> None:
        self.rounds = 0

    async def create_chat_completion(self, messages, tools):
        self.rounds += 1
        if self.rounds == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    self._call("info", "get_student_info"),
                    self._call("scores", "get_student_scores"),
                    self._call("research", "get_student_research"),
                ],
            }
        return {"role": "assistant", "content": "S001 综合评价：表现良好。", "tool_calls": []}

    @staticmethod
    def _call(call_id: str, name: str) -> dict[str, Any]:
        return {
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps({"student_id": "S001"}),
            },
        }


def _failure(code: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error_code": code, "message": code}


def _success() -> dict[str, Any]:
    return {"ok": True, "data": {"value": 1}, "error_code": None, "message": "成功"}


def test_p01_simple_query_does_not_create_complex_plan() -> None:
    assert create_plan("S001平均成绩是多少？") is None


@pytest.mark.anyio
async def test_p01_v1_entry_keeps_simple_query_task_free() -> None:
    result = await run_agent(
        "你好，请简单回答。", client=FinalAnswerClient(), session_id="p01-simple"
    )

    assert result["status"] == "completed"
    assert "task_id" not in result
    assert database.fetch_all("tasks") == []


def test_p02_complex_scholarship_plan_is_persisted_as_six_steps() -> None:
    message = "根据S001材料判断是否满足一等奖学金条件"
    plan = create_plan(message)

    assert plan is not None
    assert plan.task_type == "scholarship_evaluation"
    task = start_task(session_id="p02", user_message=message, plan=plan)
    steps = database.get_task_step_records(task["task_id"])

    assert task["status"] == "running"
    assert [step["sequence"] for step in steps] == [1, 2, 3, 4, 5, 6]
    assert [step["step_name"] for step in steps] == [
        "确认学生身份",
        "查询学生成绩",
        "查询科研成果",
        "检索奖学金评审办法",
        "执行奖学金规则判断",
        "生成有证据的解释",
    ]


def test_p03_transient_failure_retries_once_then_records_success() -> None:
    attempts = 0

    def execute_once(raw_arguments):
        nonlocal attempts
        attempts += 1
        return ({"file_id": "F1"}, _failure("TEMPORARY_IO_ERROR") if attempts == 1 else _success())

    arguments, result, retry_count = execute_with_retry(
        tool_name="query_table",
        raw_arguments={"file_id": "F1"},
        execute_once=execute_once,
        retryable=True,
        read_only=True,
        requires_confirmation=False,
    )

    assert arguments == {"file_id": "F1"}
    assert result["ok"] is True
    assert attempts == 2
    assert retry_count == 1
    plan = TaskPlan(
        task_type="retry_success",
        steps=(PlannedStep(1, "读取表格", "tool", "query_table"),),
    )
    task = start_task(session_id="p03", user_message="重试后成功", plan=plan)
    record_tool_execution(
        task_id=task["task_id"], tool_name="query_table",
        arguments=arguments, result=result, retry_count=retry_count,
    )
    assert database.get_task_step_records(task["task_id"])[0]["retry_count"] == 1


def test_p03_retrieval_not_found_uses_controlled_keyword_adjustment() -> None:
    observed_queries: list[str] = []

    def execute_once(raw_arguments):
        arguments = json.loads(raw_arguments)
        observed_queries.append(arguments["query"])
        if len(observed_queries) == 1:
            return arguments, {
                "ok": True,
                "data": {"status": "not_found"},
                "error_code": None,
                "message": "当前材料中未找到足够依据。",
            }
        return arguments, _success()

    _, result, retry_count = execute_with_retry(
        tool_name="retrieve_document",
        raw_arguments=json.dumps(
            {"scope": {"file_id": "F1"}, "query": "一等奖学金评审办法", "top_k": 5}
        ),
        execute_once=execute_once,
        retryable=True,
        read_only=True,
        requires_confirmation=False,
    )

    assert result["ok"] is True
    assert retry_count == 1
    assert observed_queries == ["一等奖学金评审办法", "一等奖学金"]


def test_p04_non_retryable_and_side_effect_failures_execute_only_once() -> None:
    for error_code, read_only, requires_confirmation in (
        ("FILE_NOT_FOUND", True, False),
        ("TEMPORARY_IO_ERROR", False, True),
    ):
        attempts = 0

        def execute_once(raw_arguments):
            nonlocal attempts
            attempts += 1
            return ({}, _failure(error_code))

        _, result, retry_count = execute_with_retry(
            tool_name="write_word" if not read_only else "query_table",
            raw_arguments={},
            execute_once=execute_once,
            retryable=True,
            read_only=read_only,
            requires_confirmation=requires_confirmation,
            max_retries=99,
        )

        assert result["error_code"] == error_code
        assert attempts == 1
        assert retry_count == 0


def test_p05_retry_never_exceeds_max_and_retry_count_is_persisted() -> None:
    attempts = 0

    def always_fails(raw_arguments):
        nonlocal attempts
        attempts += 1
        return ({"file_id": "F1"}, _failure("TABLE_READ_ERROR"))

    arguments, result, retry_count = execute_with_retry(
        tool_name="query_table",
        raw_arguments={"file_id": "F1"},
        execute_once=always_fails,
        retryable=True,
        read_only=True,
        requires_confirmation=False,
        max_retries=99,
    )
    plan = TaskPlan(
        task_type="test_retry",
        steps=(PlannedStep(1, "读取表格", "tool", "query_table"),),
    )
    task = start_task(session_id="p05", user_message="测试重试", plan=plan)
    record_tool_execution(
        task_id=task["task_id"],
        tool_name="query_table",
        arguments=arguments,
        result=result,
        retry_count=retry_count,
    )
    step = database.get_task_step_records(task["task_id"])[0]

    assert attempts == MAX_RETRIES + 1
    assert retry_count == MAX_RETRIES
    assert step["status"] == "failed"
    assert step["retry_count"] == MAX_RETRIES


def test_p06_resume_skips_successful_step_and_runs_only_remaining_step() -> None:
    plan = TaskPlan(
        task_type="resume_test",
        steps=(
            PlannedStep(1, "已完成读取", "tool", "first_tool"),
            PlannedStep(2, "待恢复读取", "tool", "second_tool"),
        ),
    )
    task = start_task(session_id="p06", user_message="恢复任务", plan=plan)
    record_tool_execution(
        task_id=task["task_id"],
        tool_name="first_tool",
        arguments={"value": 1},
        result=_success(),
        retry_count=0,
    )
    database.update_task_record(
        task["task_id"], status="failed", updated_at=database.utc_now(),
        error_code="TEMPORARY_IO_ERROR",
    )
    called: list[str] = []

    def executor(step):
        called.append(step["tool_name"])
        return _success(), 0

    result = resume_task(task["task_id"], executor)
    steps = database.get_task_step_records(task["task_id"])

    assert result["ok"] is True
    assert result["data"]["status"] == "success"
    assert called == ["second_tool"]
    assert [step["status"] for step in steps] == ["success", "success"]


@pytest.mark.anyio
async def test_p06_confirmation_resume_does_not_repeat_successful_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = WriteRequestClient()
    result = await run_agent(
        "把S001评价写进综合评价.docx",
        client=client,
        session_id="p06-confirmation",
    )
    task_id = result["task_id"]
    action_id = result["pending_action"]["action_id"]
    before = database.get_task_step_records(task_id)

    assert result["status"] == "confirmation_required"
    assert database.get_task_record(task_id)["status"] == "waiting_confirmation"
    assert [step["status"] for step in before[:6]] == [
        "success", "success", "success", "success", "success", "waiting_confirmation"
    ]
    monkeypatch.setattr(
        confirmation,
        "write_word",
        lambda file_name, content: {
            "ok": True,
            "data": {"file_name": file_name},
            "error_code": None,
            "message": "模拟幂等写入成功",
        },
    )

    confirmed = confirmation.confirm_action(action_id)
    after = database.get_task_step_records(task_id)

    assert confirmed["ok"] is True
    assert client.rounds == 2
    assert database.get_task_record(task_id)["status"] == "success"
    assert [step["status"] for step in after] == ["success"] * 7
    assert after[-1]["retry_count"] == 0
    assert [step["result_summary"] for step in after[:3]] == [
        step["result_summary"] for step in before[:3]
    ]
