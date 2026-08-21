"""Deterministic summaries for observable Trace and Workflow records."""

import json
from collections import Counter
from typing import Any

from backend import database
from backend.tools.excel_utils import failure, success


def evaluate_task(task_id: str) -> dict[str, Any]:
    """Summarize one persisted Workflow without grading hidden reasoning."""
    task = database.get_task_record(str(task_id).strip())
    if task is None:
        return failure("TASK_NOT_FOUND", "未找到指定任务")
    traces = database.get_task_trace_records(task["task_id"])
    steps = database.get_task_step_records(task["task_id"])
    return success(
        _summary(
            traces,
            workflow={
                "task_id": task["task_id"],
                "task_status": task["status"],
                "steps": steps,
            },
        ),
        "Task 评估指标读取成功",
    )


def evaluate_session(session_id: str, limit: int = 500) -> dict[str, Any]:
    """Summarize bounded session traces, including LLM token and cost usage."""
    traces = database.get_session_trace_records(str(session_id), limit)
    return success(_summary(traces, workflow=None), "Session 评估指标读取成功")


def summarize_case_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate explicit evaluation success/error rates from case outcomes."""
    case_ids = [str(item.get("case_id") or "").strip() for item in results]
    if not results or any(not case_id for case_id in case_ids):
        raise ValueError("评估结果必须包含非空 case_id")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("评估 case_id 不能重复")
    passed = sum(item.get("passed") is True for item in results)
    total = len(results)
    return {
        "total_cases": total,
        "successful_cases": passed,
        "failed_cases": total - passed,
        "success_rate": round(passed / total, 6),
        "error_rate": round((total - passed) / total, 6),
    }


def _summary(
    traces: list[dict[str, Any]],
    *,
    workflow: dict[str, Any] | None,
) -> dict[str, Any]:
    tool_traces = [item for item in traces if item["event_type"] == "tool_execution"]
    llm_traces = [item for item in traces if item["event_type"] == "llm_call"]
    retrieval = [
        metrics
        for metrics in (_metrics(item) for item in traces)
        if metrics.get("retrieval_mode")
    ]
    successful_tools = sum(item["result_status"] == "success" for item in tool_traces)
    failed_tools = len(tool_traces) - successful_tools
    tool_count = len(tool_traces)
    durations = [max(0, int(item.get("duration_ms") or 0)) for item in tool_traces]
    top_scores = [
        float(item["top_score"])
        for item in retrieval
        if isinstance(item.get("top_score"), (int, float))
    ]
    result_counts = [max(0, int(item.get("result_count") or 0)) for item in retrieval]
    summary = {
        "tool": {
            "calls": tool_count,
            "successful_calls": successful_tools,
            "failed_calls": failed_tools,
            "success_rate": round(successful_tools / tool_count, 6) if tool_count else 0.0,
            "error_rate": round(failed_tools / tool_count, 6) if tool_count else 0.0,
            "total_duration_ms": sum(durations),
            "average_duration_ms": round(sum(durations) / tool_count, 3) if tool_count else 0.0,
            "max_duration_ms": max(durations, default=0),
        },
        "retrieval": {
            "calls": len(retrieval),
            "fallback_calls": sum(bool(item.get("fallback_used")) for item in retrieval),
            "fallback_rate": (
                round(sum(bool(item.get("fallback_used")) for item in retrieval) / len(retrieval), 6)
                if retrieval
                else 0.0
            ),
            "empty_result_calls": sum(count == 0 for count in result_counts),
            "average_result_count": (
                round(sum(result_counts) / len(result_counts), 3)
                if result_counts
                else 0.0
            ),
            "average_top_score": (
                round(sum(top_scores) / len(top_scores), 6) if top_scores else None
            ),
        },
        "tokens": {
            "llm_calls": len(llm_traces),
            "input_tokens": sum(int(item.get("input_tokens") or 0) for item in llm_traces),
            "output_tokens": sum(int(item.get("output_tokens") or 0) for item in llm_traces),
            "total_tokens": sum(int(item.get("total_tokens") or 0) for item in llm_traces),
        },
        "cost": {
            "currency": "USD",
            "total_cost": round(
                sum(float(item.get("cost_usd") or 0.0) for item in llm_traces),
                10,
            ),
            "pricing_configured": any(
                bool(_metrics(item).get("pricing_configured")) for item in llm_traces
            ),
        },
        "workflow": _workflow_summary(workflow),
    }
    return summary


def _workflow_summary(workflow: dict[str, Any] | None) -> dict[str, Any] | None:
    if workflow is None:
        return None
    steps = workflow["steps"]
    statuses = Counter(str(step["status"]) for step in steps)
    completed = statuses.get("success", 0)
    return {
        "task_id": workflow["task_id"],
        "task_status": workflow["task_status"],
        "total_steps": len(steps),
        "status_counts": dict(sorted(statuses.items())),
        "completion_rate": round(completed / len(steps), 6) if steps else 0.0,
    }


def _metrics(trace: dict[str, Any]) -> dict[str, Any]:
    raw = trace.get("metrics_json")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
