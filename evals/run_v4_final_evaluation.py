"""Run the frozen V4 fixed dataset and publish observed, reproducible metrics."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_ROOT / "evals" / "v4_final_manifest.json"
DEMO_MANIFEST = PROJECT_ROOT / "evals" / "v4_demo_manifest.json"
DATASET = PROJECT_ROOT / "evals" / "v4_evaluation_dataset.json"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def calculate_rate(passed: int, total: int) -> float | None:
    return round(passed / total, 6) if total else None


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(max(0.0, float(value)) for value in values)
    rank = max(1, int(len(ordered) * quantile + 0.999999))
    return round(ordered[min(rank, len(ordered)) - 1], 3)


def run_group(name: str, nodes: list[str]) -> tuple[dict[str, Any], list[float]]:
    with tempfile.TemporaryDirectory(prefix="v4-eval-") as directory:
        report = Path(directory) / "junit.xml"
        started = time.perf_counter()
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *nodes, f"--junitxml={report}"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
        )
        wall_ms = max(0, int((time.perf_counter() - started) * 1000))
        if not report.is_file():
            raise RuntimeError(f"{name} 未生成 JUnit：{completed.stdout}\n{completed.stderr}")
        root = ET.parse(report).getroot()
        cases = list(root.iter("testcase"))
        failed = sum(
            case.find("failure") is not None or case.find("error") is not None
            for case in cases
        )
        skipped = sum(case.find("skipped") is not None for case in cases)
        passed = len(cases) - failed - skipped
        durations = [float(case.attrib.get("time") or 0.0) * 1000 for case in cases]
        return {
            "passed": passed, "failed": failed, "skipped": skipped,
            "total": len(cases), "rate": calculate_rate(passed, len(cases)),
            "status": "PASS" if completed.returncode == 0 and failed == 0 else "FAIL",
            "wall_duration_ms": wall_ms,
        }, durations


def _need(*, minimum_evidence: int = 1, required_terms: list[str] | None = None) -> dict[str, Any]:
    return {
        "need_id": "need-v4-final", "type": "rule", "name": "专项审批规则",
        "description": "确认虚构项目专项审批规则与阈值", "status": "missing",
        "scope": {}, "evidence_ids": [], "query": "虚构项目专项审批阈值",
        "minimum_evidence": minimum_evidence, "required_terms": required_terms or [],
        "rewrite_queries": [],
    }


def _retrieval(evidence: list[dict[str, Any]], candidates: int | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {"status": "found" if evidence else "not_found", "evidence": evidence,
                 "top_n_count": len(evidence) if candidates is None else candidates},
        "evidence": evidence, "error_code": None, "message": "完成",
    }


def observed_retrieval_metrics() -> dict[str, Any]:
    """Execute production control logic; values are observations, not fixtures."""
    from backend.services.agentic_retrieval import run_controlled_retrieval

    simple = run_controlled_retrieval(
        "查询虚构培养方案",
        retriever=lambda scope, query, top_k=5: (_ for _ in ()).throw(
            AssertionError("simple query must not retrieve")
        ),
    )["data"]
    sequence = 0

    def two_round(scope, query, top_k=5):
        nonlocal sequence
        sequence += 1
        text = "阈值150万元" if sequence == 1 else "需要专项审批"
        return _retrieval([{
            "evidence_id": f"ev-v4-{sequence}", "source_type": "unstructured",
            "file_id": "a" * 32, "file_name": "虚构项目管理办法.pdf",
            "page_no": 1, "text_excerpt": text,
        }], candidates=3)

    completed = run_controlled_retrieval(
        "复杂规则核验", needs=[_need(minimum_evidence=2, required_terms=["阈值", "专项审批"])],
        retriever=two_round,
    )["data"]
    budgeted = run_controlled_retrieval(
        "复杂规则核验", needs=[_need(minimum_evidence=3)], max_tool_calls=1,
        retriever=lambda scope, query, top_k=5: _retrieval([{
            "evidence_id": "ev-budget", "source_type": "unstructured",
            "file_id": "b" * 32, "file_name": "虚构规则.pdf", "text_excerpt": "部分依据",
        }]),
    )["data"]
    controlled = [completed, budgeted]
    return {
        "sample_count": 3,
        "average_retrieval_rounds": round(
            sum(len(item["rounds"]) for item in controlled) / len(controlled), 3
        ),
        "unnecessary_retrieval_rate": {
            "unnecessary": simple["tool_calls"], "simple_queries": 1,
            "rate": calculate_rate(simple["tool_calls"], 1),
        },
        "budget_termination_rate": {
            "terminated_by_budget": int(budgeted["stop_reason"] == "budget"),
            "budget_cases": 1,
            "rate": calculate_rate(int(budgeted["stop_reason"] == "budget"), 1),
        },
        "completion": {
            "stop_reason": completed["stop_reason"], "rounds": len(completed["rounds"]),
            "tool_calls": completed["tool_calls"], "answer_status": completed["answer_status"],
        },
    }


def observed_call_counts() -> dict[str, Any]:
    """Measure counters through production Trace/Evaluation over an isolated workload."""
    from backend import database
    from backend.services.evaluation_service import evaluate_session
    from backend.services.trace_service import record_trace

    with tempfile.TemporaryDirectory(prefix="v4-trace-eval-") as directory:
        original = database.DB_PATH
        database.DB_PATH = Path(directory) / "app.db"
        try:
            database.initialize_database()
            session = "v4-final-observed-workload"
            record_trace(session_id=session, event_type="tool_execution", tool_name="query_table",
                         result={"ok": True, "status": "success"}, result_status="success", duration_ms=4)
            record_trace(session_id=session, event_type="llm_call", tool_name="llm",
                         result={"ok": True, "status": "success"}, result_status="success", duration_ms=6,
                         metrics={"usage_available": False, "pricing_configured": False})
            record_trace(session_id=session, event_type="visual_ocr", tool_name="ocr",
                         result={"ok": True, "status": "success"}, result_status="success", duration_ms=5,
                         metrics={"ocr_call_count": 1, "visual_processing_duration_ms": 5,
                                  "page_count": 1, "image_count": 1, "region_count": 2,
                                  "ocr_success_count": 2, "ocr_failure_count": 0})
            record_trace(session_id=session, event_type="visual_classification", tool_name="vision",
                         result={"ok": True, "status": "success"}, result_status="success", duration_ms=3,
                         metrics={"vision_call_count": 1, "classification_status": "success"})
            summary = evaluate_session(session)["data"]
            return {
                **summary["counts"], "token_usage": summary["tokens"]["availability"],
                "estimated_cost": summary["cost"]["availability"],
            }
        finally:
            database.DB_PATH = original


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    demo_manifest = json.loads(DEMO_MANIFEST.read_text(encoding="utf-8"))
    metrics: dict[str, Any] = {}
    demos: dict[str, Any] = {}
    durations: list[float] = []
    for name, nodes in manifest.items():
        metrics[name], samples = run_group(name, nodes)
        durations.extend(samples)
    for name, nodes in demo_manifest.items():
        demos[name], _ = run_group(name, nodes)
    output = {
        "dataset": DATASET.name, "dataset_case_count": len(dataset["cases"]),
        "execution_mode": dataset["execution_mode"],
        "generated_at": datetime.now(timezone.utc).isoformat(), "metrics": metrics,
        "latency": {"sample_count": len(durations),
                    "average_ms": round(sum(durations) / len(durations), 3) if durations else None,
                    "p95_ms": percentile(durations, 0.95)},
        "retrieval_observations": observed_retrieval_metrics(),
        "call_counts": observed_call_counts(), "demos": demos,
    }
    output["status"] = "PASS" if all(
        item["status"] == "PASS" for item in [*metrics.values(), *demos.values()]
    ) else "FAIL"
    print(json.dumps(output, ensure_ascii=False, indent=None if args.compact else 2))
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
