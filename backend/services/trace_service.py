"""Create bounded observable Trace events without model reasoning content."""

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
) -> dict[str, Any]:
    trace = {
        "trace_id": uuid4().hex,
        "task_id": task_id,
        "session_id": str(session_id),
        "step_id": step_id,
        "event_type": str(event_type)[:64],
        "tool_name": str(tool_name)[:128] if tool_name else None,
        "arguments_summary": redacted_json(arguments, max_length=1000),
        "result_summary": _result_summary(result),
        "duration_ms": max(0, int(duration_ms)),
        "retry_count": max(0, int(retry_count)),
        "result_status": str(result_status)[:32],
        "error_code": redact_text(str(error_code))[:128] if error_code else None,
        "created_at": database.utc_now(),
    }
    database.add_trace_record(trace)
    return trace


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
        return redacted_json(compact, max_length=1000)
    return redacted_json(result, max_length=1000)
