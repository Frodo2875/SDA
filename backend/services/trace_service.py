"""Create bounded observable Trace events without model reasoning content."""

import os
from typing import Any
from uuid import uuid4

from backend import database
from backend.services.redaction import redacted_json, redact_text


def record_trace(
    *,
    session_id: str,
    event_type: str,
    result_status: str,
    task_id: str | None = None,
    step_id: str | None = None,
    tool_name: str | None = None,
    arguments: Any = None,
    result: Any = None,
    duration_ms: int = 0,
    retry_count: int = 0,
    error_code: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int | None = None,
    cost_usd: float = 0.0,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_input = max(0, int(input_tokens))
    safe_output = max(0, int(output_tokens))
    safe_total = max(safe_input + safe_output, int(total_tokens or 0))
    runtime_metrics = _runtime_metrics(tool_name, result)
    runtime_metrics.update(_strip_hidden_reasoning(metrics or {}))
    runtime_metrics.update(_web_observability_metrics(
        tool_name=tool_name,
        arguments=arguments,
        result=result,
        result_status=result_status,
        duration_ms=duration_ms,
        current=runtime_metrics,
    ))
    trace = {
        "trace_id": uuid4().hex,
        "task_id": task_id,
        "session_id": str(session_id),
        "step_id": step_id,
        "event_type": str(event_type)[:64],
        "tool_name": str(tool_name)[:128] if tool_name else None,
        "arguments_summary": redacted_json(
            _strip_hidden_reasoning(arguments), max_length=1000
        ),
        "result_summary": _result_summary(result),
        "duration_ms": max(0, int(duration_ms)),
        "retry_count": max(0, int(retry_count)),
        "result_status": str(result_status)[:32],
        "error_code": redact_text(str(error_code))[:128] if error_code else None,
        "created_at": database.utc_now(),
        "input_tokens": safe_input,
        "output_tokens": safe_output,
        "total_tokens": safe_total,
        "cost_usd": round(max(0.0, float(cost_usd)), 10),
        "metrics_json": redacted_json(runtime_metrics, max_length=2000),
    }
    database.add_trace_record(trace)
    return trace


def llm_usage_metrics(usage: Any) -> dict[str, Any]:
    """Normalize provider usage and estimate cost only from configured rates."""
    data = usage if isinstance(usage, dict) else {}
    usage_available = any(
        key in data
        for key in (
            "prompt_tokens", "input_tokens", "completion_tokens",
            "output_tokens", "total_tokens",
        )
    )
    input_tokens = _token_value(data, "prompt_tokens", "input_tokens")
    output_tokens = _token_value(data, "completion_tokens", "output_tokens")
    total_tokens = max(
        input_tokens + output_tokens,
        _token_value(data, "total_tokens"),
    )
    input_rate = _nonnegative_env_float("LLM_INPUT_COST_PER_1M")
    output_rate = _nonnegative_env_float("LLM_OUTPUT_COST_PER_1M")
    cost_usd = (
        input_tokens * input_rate + output_tokens * output_rate
    ) / 1_000_000
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost_usd": round(cost_usd, 10),
        "pricing_configured": input_rate > 0 or output_rate > 0,
        "usage_available": usage_available,
    }


def _result_summary(result: Any) -> str:
    if isinstance(result, dict):
        compact = {
            "ok": result.get("ok"),
            "status": (result.get("data") or {}).get("status")
            if isinstance(result.get("data"), dict)
            else result.get("status"),
            "message": result.get("result_summary") or result.get("message"),
            "error_code": result.get("error_code"),
        }
        for key in ("approval_id", "approval_result"):
            if result.get(key) is not None:
                compact[key] = result[key]
        return redacted_json(compact, max_length=1000)
    return redacted_json(result, max_length=1000)


def _runtime_metrics(tool_name: str | None, result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if tool_name == "retrieve_web":
        evidence = (
            data.get("evidence_chain")
            if isinstance(data.get("evidence_chain"), list)
            else []
        )
        detected_patterns = {
            str(pattern)
            for item in evidence
            if isinstance(item, dict)
            for pattern in item.get("detected_untrusted_patterns") or []
        }
        return {
            "retrieval_mode": data.get("mode"),
            "retrieval_status": data.get("status"),
            "result_count": len(evidence),
            "search_query": data.get("query"),
            "url": data.get("url"),
            "fetch_status": data.get("status"),
            "latency_ms": _optional_duration(data.get("latency_ms")),
            "evidence_count": len(evidence),
            "untrusted_content": True,
            "detected_untrusted_patterns": sorted(detected_patterns),
            "evidence_ids": [
                str(item.get("evidence_id"))
                for item in evidence
                if isinstance(item, dict) and item.get("evidence_id")
            ],
            "evidence_source_types": sorted({
                str(item.get("source_type"))
                for item in evidence
                if isinstance(item, dict) and item.get("source_type")
            }),
        }
    if tool_name != "retrieve_document":
        return {}
    evidence = data.get("evidence") if isinstance(data.get("evidence"), list) else []
    scores = [
        float(item["score"])
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("score"), (int, float))
    ]
    return {
        "retrieval_mode": data.get("retrieval_mode"),
        "fallback_used": bool(data.get("fallback_used")),
        "fallback_reason": data.get("fallback_reason"),
        "rerank_fallback": bool(data.get("rerank_fallback")),
        "retrieval_status": data.get("status"),
        "result_count": len(evidence),
        "keyword_candidate_count": int(data.get("keyword_candidate_count") or 0),
        "vector_candidate_count": int(data.get("vector_candidate_count") or 0),
        "top_n_count": int(data.get("top_n_count") or 0),
        "top_k_count": int(data.get("top_k_count") or len(evidence)),
        "hybrid_retrieval_duration_ms": _optional_duration(
            data.get("hybrid_retrieval_duration_ms")
        ),
        "rerank_duration_ms": _optional_duration(data.get("rerank_duration_ms")),
        "evidence_ids": [
            str(item.get("evidence_id"))
            for item in evidence
            if isinstance(item, dict) and item.get("evidence_id")
        ],
        "top_score": max(scores) if scores else None,
    }


def _web_observability_metrics(
    *,
    tool_name: str | None,
    arguments: Any,
    result: Any,
    result_status: str,
    duration_ms: int,
    current: dict[str, Any],
) -> dict[str, Any]:
    """Complete Web Trace fields in middleware, outside retrieval modules."""
    if tool_name != "retrieve_web":
        return {}
    args = arguments if isinstance(arguments, dict) else {}
    data = result.get("data") if isinstance(result, dict) and isinstance(
        result.get("data"), dict
    ) else {}
    observed = dict(current)
    if observed.get("source_strategy") is None:
        observed["source_strategy"] = "DIRECT_URL" if args.get("url") else "WEB"
    if observed.get("search_query") is None:
        observed["search_query"] = args.get("query")
    if observed.get("url") is None:
        observed["url"] = args.get("url")
    if observed.get("fetch_status") is None:
        observed["fetch_status"] = data.get("status") or result_status
    if observed.get("latency_ms") is None:
        observed["latency_ms"] = max(0, int(duration_ms))
    if observed.get("evidence_count") is None:
        observed["evidence_count"] = max(
            0, int(observed.get("new_evidence_count") or 0)
        )
    observed["untrusted_content"] = True
    return observed


_HIDDEN_REASONING_KEYS = frozenset({
    "chain_of_thought", "chain-of-thought", "cot", "reasoning",
    "hidden_reasoning", "thinking", "system_prompt", "full_prompt",
})


def _strip_hidden_reasoning(value: Any) -> Any:
    """Keep observable outcomes while refusing hidden reasoning payload keys."""
    if isinstance(value, dict):
        return {
            key: _strip_hidden_reasoning(item)
            for key, item in value.items()
            if str(key).strip().casefold() not in _HIDDEN_REASONING_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_strip_hidden_reasoning(item) for item in value]
    return value


def _optional_duration(value: Any) -> int | None:
    if not isinstance(value, (int, float)):
        return None
    return max(0, int(value))


def _token_value(data: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return 0


def _nonnegative_env_float(name: str) -> float:
    try:
        return max(0.0, float(os.getenv(name, "0") or 0))
    except ValueError:
        return 0.0
