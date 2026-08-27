"""Run the fixed V3 acceptance dataset and publish only observed metrics."""

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
MANIFEST = PROJECT_ROOT / "evals" / "v3_final_manifest.json"
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


def run_metric(name: str, nodes: list[str]) -> tuple[dict[str, Any], list[float]]:
    with tempfile.TemporaryDirectory(prefix="v3-eval-") as directory:
        report = Path(directory) / "junit.xml"
        started = time.perf_counter()
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *nodes,
                f"--junitxml={report}",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        wall_ms = max(0, int((time.perf_counter() - started) * 1000))
        if not report.is_file():
            raise RuntimeError(
                f"{name} 未生成 JUnit 结果：{completed.stdout}\n{completed.stderr}"
            )
        root = ET.parse(report).getroot()
        cases = list(root.iter("testcase"))
        failed = sum(
            case.find("failure") is not None or case.find("error") is not None
            for case in cases
        )
        skipped = sum(case.find("skipped") is not None for case in cases)
        passed = len(cases) - failed - skipped
        durations = [float(case.attrib.get("time") or 0.0) * 1000 for case in cases]
        result = {
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total": len(cases),
            "rate": calculate_rate(passed, len(cases)),
            "status": "PASS" if completed.returncode == 0 and failed == 0 else "FAIL",
            "wall_duration_ms": wall_ms,
        }
        return result, durations


def observed_trace_workload() -> dict[str, Any]:
    """Measure call counters through the production Trace/Evaluation services."""
    from backend import database
    from backend.services.evaluation_service import evaluate_session
    from backend.services.trace_service import record_trace

    with tempfile.TemporaryDirectory(prefix="v3-trace-eval-") as directory:
        original = database.DB_PATH
        database.DB_PATH = Path(directory) / "app.db"
        try:
            database.initialize_database()
            session_id = "v3-final-observability-workload"
            for index, duration in enumerate((7, 11), start=1):
                record_trace(
                    session_id=session_id,
                    event_type="tool_execution",
                    tool_name=f"eval_tool_{index}",
                    result={"ok": True, "status": "success"},
                    duration_ms=duration,
                    result_status="success",
                )
            record_trace(
                session_id=session_id,
                event_type="llm_call",
                tool_name="llm",
                result={"ok": True, "status": "success"},
                duration_ms=13,
                result_status="success",
                metrics={"usage_available": False, "pricing_configured": False},
            )
            summary = evaluate_session(session_id)["data"]
            return {
                "tool_call_count": summary["counts"]["tool_calls"],
                "llm_call_count": summary["counts"]["llm_calls"],
                "token_usage": summary["tokens"]["availability"],
                "estimated_cost": summary["cost"]["availability"],
            }
        finally:
            database.DB_PATH = original


def observed_domain_metrics() -> dict[str, Any]:
    """Execute small deterministic domain fixtures through production services."""
    from backend.runtime.safety_policy import (
        PolicyDecision,
        assess_high_risk_action,
    )
    from backend.services import ocr_service
    from backend.table_structure import build_simple_table

    class FixedEngine:
        def __call__(self, image: int):
            page = int(image)
            return [
                [
                    [[1, 2], [11, 2], [11, 12], [1, 12]],
                    f"关键字段-{page}",
                    0.9,
                ]
            ], None

    original_render = ocr_service._render_pdf_pages
    original_engine = ocr_service._create_engine
    try:
        ocr_service._render_pdf_pages = lambda path, pages=None: [
            (page, page) for page in (pages or [1, 2, 3])
        ]
        ocr_service._create_engine = lambda: FixedEngine()
        ocr = ocr_service.ocr_document(Path("fixed-eval.pdf"))
    finally:
        ocr_service._render_pdf_pages = original_render
        ocr_service._create_engine = original_engine
    pages = (ocr.get("data") or {}).get("pages") or []
    page_passed = sum(page.get("status") == "success" for page in pages)
    key_field_passed = sum(
        [
            page.get("text") == f"关键字段-{page.get('page_no')}",
            page.get("bbox") == [1.0, 2.0, 11.0, 12.0],
            page.get("confidence") == 0.9,
        ].count(True)
        for page in pages
    )

    expected_rows = [["姓名", "成绩"], ["张三", "90"]]
    table = build_simple_table(
        table_id="v3-final-table",
        file_id="v3-final-file",
        rows=expected_rows,
        page_no=1,
        source_parser="v3-final-eval",
    )
    table_passed = sum(
        cell.cell_text == expected_rows[cell.row_index][cell.column_index]
        for cell in table.cells
    )

    operations = ("write_word", "delete_file", "overwrite", "undo_word", "rollback_word")
    approval_passed = sum(
        assess_high_risk_action(action_type=operation).decision
        == PolicyDecision.CONFIRMATION_REQUIRED
        for operation in operations
    )
    return {
        "ocr_page_success_rate": _domain_rate(page_passed, len(pages)),
        "ocr_key_field_accuracy": _domain_rate(key_field_passed, len(pages) * 3),
        "table_structure_accuracy": _domain_rate(table_passed, len(table.cells)),
        "high_risk_approval_trigger_rate": _domain_rate(
            approval_passed, len(operations)
        ),
    }


def _domain_rate(passed: int, total: int) -> dict[str, Any]:
    return {"passed": passed, "total": total, "rate": calculate_rate(passed, total)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    metrics: dict[str, Any] = {}
    durations: list[float] = []
    for name, nodes in manifest.items():
        metrics[name], samples = run_metric(name, nodes)
        durations.extend(samples)
    output = {
        "dataset": "v3_final_manifest.json",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "latency": {
            "sample_count": len(durations),
            "average_ms": round(sum(durations) / len(durations), 3) if durations else None,
            "p95_ms": percentile(durations, 0.95),
        },
        "trace_workload": observed_trace_workload(),
        "domain_metrics": observed_domain_metrics(),
        "status": "PASS" if all(item["status"] == "PASS" for item in metrics.values()) else "FAIL",
    }
    print(json.dumps(output, ensure_ascii=False, indent=None if args.compact else 2))
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
