"""V3 final Evaluation manifest and deterministic calculator contracts."""

import ast
import importlib.util
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PROJECT_ROOT / "evals" / "v3_final_manifest.json"
TRACEABILITY = PROJECT_ROOT / "evals" / "v3_requirement_traceability.json"
RESULTS = PROJECT_ROOT / "evals" / "v3_final_results.json"
SPEC = importlib.util.spec_from_file_location(
    "v3_final_evaluation_runner",
    PROJECT_ROOT / "evals" / "run_v3_final_evaluation.py",
)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def test_v3_final_metric_manifest_references_real_tests() -> None:
    metrics = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert set(metrics) == {
        "ocr_page_success_rate",
        "ocr_key_field_accuracy",
        "table_structure_accuracy",
        "hybrid_recall_at_k",
        "rerank_top_k_hit_rate",
        "evidence_localization_accuracy",
        "workflow_success_rate",
        "resume_success_rate",
        "async_retry_resume_success_rate",
        "prompt_injection_defense_rate",
        "high_risk_approval_trigger_rate",
    }
    for nodes in metrics.values():
        assert nodes
        for node_id in nodes:
            file_name, function_name = node_id.split("::", maxsplit=1)
            path = PROJECT_ROOT / file_name
            assert path.is_file(), node_id
            functions = {
                node.name
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert function_name in functions, node_id


def test_v3_final_metric_calculators_use_observed_samples() -> None:
    assert RUNNER.calculate_rate(9, 10) == 0.9
    assert RUNNER.calculate_rate(0, 0) is None
    assert RUNNER.percentile([4, 1, 3, 2, 100], 0.95) == 100
    assert RUNNER.percentile([], 0.95) is None


def test_v3_requirement_traceability_covers_every_formal_id() -> None:
    mapping = json.loads(TRACEABILITY.read_text(encoding="utf-8"))
    expected = {
        *(f"F{index:02d}" for index in range(1, 13)),
        *(f"O{index:02d}" for index in range(1, 11)),
        *(f"R{index}" for index in range(201, 211)),
        *(f"WF{index:02d}" for index in range(1, 11)),
        *(f"S{index:02d}" for index in range(1, 11)),
    }
    assert set(mapping) == expected
    for requirement_id, nodes in mapping.items():
        assert nodes, requirement_id
        for node_id in nodes:
            file_name, function_name = node_id.split("::", maxsplit=1)
            path = PROJECT_ROOT / file_name
            assert path.is_file(), node_id
            functions = {
                node.name
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert function_name in functions, node_id


def test_v3_final_result_snapshot_has_observed_denominators() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    result = json.loads(RESULTS.read_text(encoding="utf-8"))
    assert result["dataset"] == MANIFEST.name
    assert result["status"] == "PASS"
    assert set(result["metrics"]) == set(manifest)
    for metric in result["metrics"].values():
        assert metric["total"] > 0
        assert metric["passed"] + metric["failed"] + metric["skipped"] == metric["total"]
        assert metric["rate"] == RUNNER.calculate_rate(metric["passed"], metric["total"])
    assert result["latency"]["sample_count"] > 0
    assert result["trace_workload"]["tool_call_count"] >= 0
    assert result["trace_workload"]["llm_call_count"] >= 0
    assert result["domain_metrics"] == {
        "ocr_page_success_rate": {"passed": 3, "total": 3, "rate": 1.0},
        "ocr_key_field_accuracy": {"passed": 9, "total": 9, "rate": 1.0},
        "table_structure_accuracy": {"passed": 4, "total": 4, "rate": 1.0},
        "high_risk_approval_trigger_rate": {"passed": 5, "total": 5, "rate": 1.0},
    }
