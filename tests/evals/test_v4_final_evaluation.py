"""V4.11 frozen dataset, manifest, runner, demo and result contracts."""

import ast
import importlib.util
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALS = PROJECT_ROOT / "evals"
DATASET = EVALS / "v4_evaluation_dataset.json"
MANIFEST = EVALS / "v4_final_manifest.json"
DEMOS = EVALS / "v4_demo_manifest.json"
RESULTS = EVALS / "v4_final_results.json"
SPEC = importlib.util.spec_from_file_location(
    "v4_final_evaluation_runner", EVALS / "run_v4_final_evaluation.py"
)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def _assert_real_nodes(mapping: dict[str, list[str]]) -> None:
    for name, nodes in mapping.items():
        assert nodes, name
        for node_id in nodes:
            file_name, function_name = node_id.split("::", maxsplit=1)
            path = PROJECT_ROOT / file_name
            assert path.is_file(), node_id
            functions = {
                node.name for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert function_name in functions, node_id


def test_v4_fixed_dataset_is_complete_unique_and_fictional() -> None:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    kinds = {item["kind"] for item in cases}
    assert len(cases) >= 16
    assert len({item["case_id"] for item in cases}) == len(cases)
    assert all(item["fictional"] is True for item in cases)
    assert {
        "printed_notification_image", "student_application_form_image",
        "clear_handwriting", "medium_handwriting", "extremely_unclear_handwriting",
        "certificate_image", "image_transcript_pdf", "mixed_visual_text_pdf",
        "printed_table_image", "handwritten_table", "complex_table_fallback",
        "employee_general_excel", "project_budget_excel", "project_policy_pdf",
        "meeting_minutes_word", "visual_prompt_injection",
    } <= kinds
    assert dataset["execution_mode"] == "offline_fixed_adapters"


def test_v4_metric_and_demo_manifests_reference_real_tests() -> None:
    metrics = json.loads(MANIFEST.read_text(encoding="utf-8"))
    demos = json.loads(DEMOS.read_text(encoding="utf-8"))
    assert set(metrics) == {
        "printed_ocr_usable_rate", "handwriting_usable_rate",
        "low_confidence_safety_rate", "visual_block_accuracy", "kie_field_accuracy",
        "visual_table_cell_accuracy", "visual_evidence_localization_rate",
        "general_task_success_rate", "domain_routing_accuracy",
        "evidence_sufficiency_accuracy", "retrieval_completion_rate",
        "visual_prompt_injection_defense_rate",
    }
    assert set(demos) == {
        "demo_a_visual_handwriting", "demo_b_general_document_agent",
        "demo_c_controlled_agentic_retrieval",
    }
    _assert_real_nodes(metrics)
    _assert_real_nodes(demos)


def test_v4_calculators_and_control_observations_use_executed_samples() -> None:
    assert RUNNER.calculate_rate(9, 10) == 0.9
    assert RUNNER.calculate_rate(0, 0) is None
    assert RUNNER.percentile([1, 2, 3, 4, 100], 0.95) == 100
    observations = RUNNER.observed_retrieval_metrics()
    assert observations["completion"] == {
        "stop_reason": "sufficient", "rounds": 2,
        "tool_calls": 2, "answer_status": "confirmed",
    }
    assert observations["unnecessary_retrieval_rate"]["rate"] == 0.0
    assert observations["budget_termination_rate"]["rate"] == 1.0


def test_v4_result_snapshot_has_observed_denominators_and_no_fake_usage() -> None:
    result = json.loads(RESULTS.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert result["dataset"] == DATASET.name
    assert result["dataset_case_count"] >= 16
    assert result["status"] == "PASS"
    assert set(result["metrics"]) == set(manifest)
    for metric in result["metrics"].values():
        assert metric["total"] > 0
        assert metric["passed"] + metric["failed"] + metric["skipped"] == metric["total"]
        assert metric["rate"] == RUNNER.calculate_rate(metric["passed"], metric["total"])
    assert result["latency"]["sample_count"] > 0
    assert result["call_counts"]["tool_calls"] == 1
    assert result["call_counts"]["llm_calls"] == 1
    assert result["call_counts"]["ocr_calls"] == 1
    assert result["call_counts"]["vision_calls"] == 1
    assert result["call_counts"]["token_usage"] == "unavailable"
    assert result["call_counts"]["estimated_cost"] == "unavailable"
    assert all(item["status"] == "PASS" for item in result["demos"].values())
