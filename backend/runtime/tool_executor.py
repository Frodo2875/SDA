"""Schema-preserving Tool execution with a strict retry ceiling."""

import json
from collections.abc import Callable
from typing import Any

from backend.runtime.policy import MAX_RETRIES, is_retryable_result


SingleAttempt = Callable[[Any], tuple[dict[str, Any], dict[str, Any]]]


def execute_with_retry(
    *,
    tool_name: str,
    raw_arguments: Any,
    execute_once: SingleAttempt,
    retryable: bool,
    read_only: bool,
    requires_confirmation: bool,
    max_retries: int = MAX_RETRIES,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Execute once plus at most MAX_RETRIES; retry_count excludes first attempt."""
    bounded_retries = max(0, min(int(max_retries), MAX_RETRIES))
    current_arguments = raw_arguments
    retry_count = 0
    while True:
        arguments, result = execute_once(current_arguments)
        if retry_count >= bounded_retries or not is_retryable_result(
            tool_name,
            result,
            retryable=retryable,
            read_only=read_only,
            requires_confirmation=requires_confirmation,
        ):
            return arguments, result, retry_count
        adjusted = _controlled_adjustment(tool_name, current_arguments, retry_count)
        if adjusted is None and result.get("error_code") is None:
            return arguments, result, retry_count
        current_arguments = adjusted if adjusted is not None else current_arguments
        retry_count += 1


def _controlled_adjustment(
    tool_name: str, raw_arguments: Any, retry_count: int
) -> str | None:
    """Only retrieval keywords have a deterministic, non-LLM retry adjustment."""
    if tool_name != "retrieve_document":
        return None
    try:
        parsed = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    query = str(parsed.get("query") or "").strip()
    candidates = _query_candidates(query)
    if retry_count >= len(candidates):
        return None
    parsed["query"] = candidates[retry_count]
    return json.dumps(parsed, ensure_ascii=False)


def _query_candidates(query: str) -> list[str]:
    candidates = []
    simplified = query
    for suffix in ("评审办法", "相关规定", "实施办法", "管理办法"):
        simplified = simplified.replace(suffix, "")
    simplified = simplified.strip(" ，。；;：:")
    if simplified and simplified != query:
        candidates.append(simplified)
    if "一等奖学金" in query and "一等奖学金" not in candidates:
        candidates.append("一等奖学金")
    return candidates[:MAX_RETRIES]
