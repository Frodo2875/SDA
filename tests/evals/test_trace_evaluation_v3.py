"""V3.10 observable Trace and evaluation metric contracts."""

import ast
import json
from pathlib import Path

import pytest

from backend import database
from backend.agent import run_agent
from backend.runtime.planner import PlannedStep, TaskPlan
from backend.runtime.task_runner import (
    finalize_task,
    record_tool_execution,
    start_task,
)
from backend.services.evaluation_service import (
    evaluate_session,
    evaluate_task,
    summarize_case_results,
)
from backend.services.trace_service import llm_usage_metrics, record_trace


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = PROJECT_ROOT / "evals"


class UsageClient:
    async def create_chat_completion(self, messages, tools):
        return {
            "role": "assistant",
            "content": "已完成。",
            "tool_calls": [],
            "_model": "offline-eval-model",
            "_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }


def test_tool_duration_and_retrieval_metrics_are_persisted() -> None:
    record_trace(
        session_id="trace-eval-retrieval",
        event_type="tool_execution",
        tool_name="retrieve_document",
        arguments={"query": "奖学金条件"},
        result={
            "ok": True,
            "data": {
                "status": "found",
                "retrieval_mode": "hybrid",
                "fallback_used": False,
                "evidence": [{"evidence_id": "ev-1", "score": 0.82}],
            },
            "error_code": None,
            "message": "检索完成",
        },
        duration_ms=37,
        result_status="success",
    )

    trace = database.get_session_trace_records("trace-eval-retrieval")[0]
    metrics = json.loads(trace["metrics_json"])
    summary = evaluate_session("trace-eval-retrieval")["data"]

    assert trace["duration_ms"] == 37
    assert metrics == {
        "retrieval_mode": "hybrid",
        "fallback_used": False,
        "retrieval_status": "found",
        "result_count": 1,
        "top_score": 0.82,
    }
    assert summary["tool"]["average_duration_ms"] == 37
    assert summary["tool"]["success_rate"] == 1.0
    assert summary["tool"]["error_rate"] == 0.0
    assert summary["retrieval"]["average_top_score"] == 0.82


def test_workflow_status_is_summarized_from_task_steps() -> None:
    plan = TaskPlan(
        task_type="trace_evaluation",
        steps=(PlannedStep(1, "读取材料", "QUERY", "query_table"),),
    )
    task = start_task(
        session_id="trace-eval-workflow",
        user_message="读取材料",
        plan=plan,
    )
    record_tool_execution(
        task_id=task["task_id"],
        tool_name="query_table",
        arguments={},
        result={"ok": True, "data": {}, "error_code": None, "message": "完成"},
        retry_count=0,
        duration_ms=25,
    )
    finalize_task(task["task_id"], {"status": "completed"})

    result = evaluate_task(task["task_id"])

    assert result["ok"] is True
    assert result["data"]["workflow"] == {
        "task_id": task["task_id"],
        "task_status": "success",
        "total_steps": 1,
        "status_counts": {"success": 1},
        "completion_rate": 1.0,
    }
    assert result["data"]["tool"]["total_duration_ms"] == 25


@pytest.mark.anyio
async def test_llm_usage_and_configured_cost_are_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_INPUT_COST_PER_1M", "2")
    monkeypatch.setenv("LLM_OUTPUT_COST_PER_1M", "4")

    result = await run_agent(
        "普通问候",
        client=UsageClient(),
        session_id="trace-eval-usage",
    )
    summary = evaluate_session("trace-eval-usage")["data"]

    assert result["status"] == "completed"
    assert summary["tokens"] == {
        "llm_calls": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
    }
    assert summary["cost"] == {
        "currency": "USD",
        "total_cost": 0.00028,
        "pricing_configured": True,
    }


def test_missing_pricing_does_not_invent_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_INPUT_COST_PER_1M", raising=False)
    monkeypatch.delenv("LLM_OUTPUT_COST_PER_1M", raising=False)

    usage = llm_usage_metrics({"prompt_tokens": 10, "completion_tokens": 5})

    assert usage["total_tokens"] == 15
    assert usage["cost_usd"] == 0.0
    assert usage["pricing_configured"] is False


def test_eval_cases_publish_success_and_error_rates() -> None:
    cases = json.loads((EVAL_ROOT / "v3_10_cases.json").read_text(encoding="utf-8"))
    published = json.loads(
        (EVAL_ROOT / "v3_10_results.json").read_text(encoding="utf-8")
    )
    observations = []
    for case in cases:
        path_text, function_name = case["test"].split("::", maxsplit=1)
        path = PROJECT_ROOT / path_text
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        observations.append(
            {"case_id": case["case_id"], "passed": function_name in functions}
        )

    summary = summarize_case_results(observations)

    assert len({case["case_id"] for case in cases}) == len(cases)
    assert all(case["metric"] for case in cases)
    assert summary == {
        key: published[key]
        for key in (
            "total_cases",
            "successful_cases",
            "failed_cases",
            "success_rate",
            "error_rate",
        )
    }
