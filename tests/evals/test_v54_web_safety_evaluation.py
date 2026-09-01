"""V5.4 offline Web safety evaluation metrics."""

import json

from backend import database
from backend.services.evaluation_service import evaluate_session
from backend.services.trace_service import record_trace


def test_v54_evaluation_summarizes_injection_and_ssrf_controls() -> None:
    record_trace(
        session_id="v54-security-eval",
        event_type="tool_execution",
        tool_name="retrieve_web",
        arguments={"query": "latest policy"},
        result={
            "ok": True,
            "data": {
                "status": "found",
                "mode": "search",
                "query": "latest policy",
                "latency_ms": 7,
                "evidence_chain": [{
                    "evidence_id": "web-injection",
                    "source_type": "WEB",
                    "detected_untrusted_patterns": ["INSTRUCTION_OVERRIDE"],
                }],
            },
        },
        result_status="success",
        metrics={"source_strategy": "WEB"},
    )
    record_trace(
        session_id="v54-security-eval",
        event_type="tool_execution",
        tool_name="retrieve_web",
        arguments={"url": "http://127.0.0.1/"},
        result={
            "ok": False,
            "data": {
                "status": "failed",
                "mode": "direct_url",
                "url": "http://127.0.0.1/",
                "latency_ms": 0,
            },
            "error_code": "WEB_SSRF_BLOCKED",
        },
        result_status="failed",
        error_code="WEB_SSRF_BLOCKED",
        metrics={"source_strategy": "DIRECT_URL"},
    )

    traces = database.get_session_trace_records("v54-security-eval")
    direct_trace = next(
        item for item in traces if item["error_code"] == "WEB_SSRF_BLOCKED"
    )
    direct_metrics = json.loads(direct_trace["metrics_json"])
    web_safety = evaluate_session("v54-security-eval")["data"]["web_safety"]

    assert direct_metrics["source_strategy"] == "DIRECT_URL"
    assert direct_metrics["url"] == "http://127.0.0.1/"
    assert direct_metrics["fetch_status"] == "failed"
    assert direct_metrics["latency_ms"] == 0
    assert direct_metrics["evidence_count"] == 0
    assert web_safety == {
        "calls": 2,
        "injection_detected_calls": 1,
        "detected_patterns": {"INSTRUCTION_OVERRIDE": 1},
        "source_strategies": {"DIRECT_URL": 1, "WEB": 1},
        "ssrf_blocked_calls": 1,
        "tool_boundary_blocks": 0,
    }
