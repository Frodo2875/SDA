"""Deterministic summaries for observable Trace and Workflow records."""

import json
from collections import Counter
from datetime import datetime, timezone
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
                "created_at": task.get("created_at"),
                "completed_at": task.get("completed_at"),
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
    all_durations = [
        max(0, int(item.get("duration_ms") or 0))
        for item in traces
        if item.get("duration_ms") is not None
    ]
    usage_available = any(
        bool(_metrics(item).get("usage_available")) for item in llm_traces
    )
    pricing_configured = any(
        bool(_metrics(item).get("pricing_configured")) for item in llm_traces
    )
    stage_metrics = _stage_summary(traces)
    observable_metrics = [_metrics(item) for item in traces]
    agentic_rounds = [
        item for item in observable_metrics if item.get("retrieval_round") is not None
    ]
    visual_metrics = [
        item for item in observable_metrics
        if item.get("visual_processing_duration_ms") is not None
        or item.get("classification_status") is not None
        or item.get("kie_status") is not None
    ]
    errors = [item for item in traces if item.get("error_code")]
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
            "p95_duration_ms": _percentile(durations, 0.95),
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
            "average_hybrid_duration_ms": _average_optional(
                retrieval, "hybrid_retrieval_duration_ms"
            ),
            "average_rerank_duration_ms": _average_optional(
                retrieval, "rerank_duration_ms"
            ),
            "average_candidate_count": _average_optional(retrieval, "top_n_count"),
            "average_top_k": _average_optional(retrieval, "top_k_count"),
        },
        "tokens": {
            "llm_calls": len(llm_traces),
            "input_tokens": sum(int(item.get("input_tokens") or 0) for item in llm_traces),
            "output_tokens": sum(int(item.get("output_tokens") or 0) for item in llm_traces),
            "total_tokens": sum(int(item.get("total_tokens") or 0) for item in llm_traces),
            "availability": "available" if usage_available else "unavailable",
        },
        "cost": {
            "currency": "USD",
            "total_cost": round(
                sum(float(item.get("cost_usd") or 0.0) for item in llm_traces),
                10,
            ),
            "pricing_configured": pricing_configured,
            "availability": (
                "available"
                if usage_available and pricing_configured
                else "unavailable"
            ),
        },
        "counts": {
            "tool_calls": len(tool_traces),
            "llm_calls": len(llm_traces),
            "ocr_calls": sum(int(item.get("ocr_call_count") or 0) for item in observable_metrics),
            "vision_calls": sum(int(item.get("vision_call_count") or 0) for item in observable_metrics),
            "retrieval_rounds": len(agentic_rounds),
            "trace_events": len(traces),
        },
        "latency": {
            "sample_count": len(all_durations),
            "average_duration_ms": (
                round(sum(all_durations) / len(all_durations), 3)
                if all_durations else None
            ),
            "p95_duration_ms": _percentile(all_durations, 0.95),
            "max_duration_ms": max(all_durations) if all_durations else None,
        },
        "stages": stage_metrics,
        "visual": {
            "processing_duration_ms": sum(
                int(item.get("visual_processing_duration_ms") or 0)
                for item in visual_metrics
            ),
            "page_count": sum(int(item.get("page_count") or 0) for item in visual_metrics),
            "image_count": sum(int(item.get("image_count") or 0) for item in visual_metrics),
            "region_count": sum(int(item.get("region_count") or 0) for item in visual_metrics),
            "ocr_success_count": sum(
                int(item.get("ocr_success_count") or 0) for item in visual_metrics
            ),
            "ocr_failure_count": sum(
                int(item.get("ocr_failure_count") or 0) for item in visual_metrics
            ),
            "handwriting_confidence": [
                float(item["handwriting_confidence"])
                for item in visual_metrics
                if isinstance(item.get("handwriting_confidence"), (int, float))
            ],
            "classification_status": [
                item["classification_status"]
                for item in visual_metrics if item.get("classification_status") is not None
            ],
            "kie_status": [
                item["kie_status"]
                for item in visual_metrics if item.get("kie_status") is not None
            ],
        },
        "agentic_retrieval": {
            "rounds": len(agentic_rounds),
            "candidate_count": sum(
                int(item.get("candidate_count") or 0) for item in agentic_rounds
            ),
            "new_evidence_count": sum(
                int(item.get("new_evidence_count") or 0) for item in agentic_rounds
            ),
            "sufficiency_transitions": [
                item["sufficiency_transition"]
                for item in agentic_rounds
                if item.get("sufficiency_transition")
            ],
            "stop_reasons": [
                item["stop_reason"]
                for item in observable_metrics if item.get("stop_reason")
            ],
        },
        "errors": {
            "count": len(errors),
            "codes": dict(sorted(Counter(str(item["error_code"]) for item in errors).items())),
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
    started_values = [step.get("started_at") for step in steps if step.get("started_at")]
    first_started = min(started_values) if started_values else None
    timing = {
        "queue_time_ms": _duration_between(workflow.get("created_at"), first_started),
        "execution_time_ms": _duration_between(first_started, workflow.get("completed_at")),
        "total_duration_ms": _duration_between(
            workflow.get("created_at"), workflow.get("completed_at")
        ),
    }
    return {
        "task_id": workflow["task_id"],
        "task_status": workflow["task_status"],
        "total_steps": len(steps),
        "status_counts": dict(sorted(statuses.items())),
        "completion_rate": round(completed / len(steps), 6) if steps else 0.0,
        "retry_count": sum(int(step.get("retry_count") or 0) for step in steps),
        "timing": timing,
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


def _stage_summary(traces: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trace in traces:
        metrics = _metrics(trace)
        stage = metrics.get("stage")
        if stage:
            grouped.setdefault(str(stage), []).append({"trace": trace, "metrics": metrics})
    result: dict[str, Any] = {}
    for stage, entries in sorted(grouped.items()):
        durations = [max(0, int(item["trace"].get("duration_ms") or 0)) for item in entries]
        latest = entries[-1]["metrics"]
        result[stage] = {
            "calls": len(entries),
            "total_duration_ms": sum(durations),
            "average_duration_ms": round(sum(durations) / len(durations), 3),
            "p95_duration_ms": _percentile(durations, 0.95),
            "latest_metrics": latest,
        }
    return result


def _average_optional(items: list[dict[str, Any]], key: str) -> float | None:
    values = [
        float(item[key]) for item in items
        if isinstance(item.get(key), (int, float))
    ]
    return round(sum(values) / len(values), 3) if values else None


def _percentile(values: list[int], quantile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int(len(ordered) * quantile + 0.999999))
    return ordered[min(rank, len(ordered)) - 1]


def _duration_between(start: Any, end: Any) -> int | None:
    left = _parse_time(start)
    right = _parse_time(end)
    if left is None or right is None:
        return None
    return max(0, int((right - left).total_seconds() * 1000))


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
