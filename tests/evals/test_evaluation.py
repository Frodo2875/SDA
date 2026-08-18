"""Fixed, offline V2 Evaluation contracts; natural-language quality stays manual."""

import ast
import json
import time
from pathlib import Path
from typing import Any

import pytest

from backend.agent import run_agent
from backend.tools.file_tools import list_files
from backend.tools.student_tools import get_student_research, get_student_scores, search_student


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = PROJECT_ROOT / "evals"
EXPECTED_IDS = {
    *(f"U{index:02d}" for index in range(1, 12)),
    *(f"R{index:02d}" for index in range(1, 6)),
    *(f"P{index:02d}" for index in range(1, 7)),
    *(f"D{index:02d}" for index in range(1, 7)),
    *(f"W{index:02d}" for index in range(1, 8)),
    *(f"B{index:02d}" for index in range(1, 6)),
    *(f"E{index:02d}" for index in range(1, 7)),
}


def _load(name: str) -> Any:
    return json.loads((EVAL_ROOT / name).read_text(encoding="utf-8"))


class ReplayEvaluationClient:
    """Replay expected tool requests; it does not grade natural-language quality."""

    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls
        self.round = 0

    async def create_chat_completion(self, messages, tools):
        self.round += 1
        if self.round == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"eval-{index}",
                        "type": "function",
                        "function": {
                            "name": item["name"],
                            "arguments": json.dumps(item["arguments"], ensure_ascii=False),
                        },
                    }
                    for index, item in enumerate(self.calls)
                ],
            }
        return {
            "role": "assistant",
            "content": "已依据本轮 Python 工具结果完成回答。",
            "tool_calls": [],
        }


def test_fixed_evaluation_data_matches_real_python_tools() -> None:
    fixed = _load("fixed_data.json")
    files = list_files()
    duplicate = search_student(fixed["duplicate_name"]["name"])
    missing_score = get_student_scores(fixed["missing_score_student_id"])
    missing_research = get_student_research(fixed["missing_research_student_id"])
    unknown = search_student(fixed["unknown_student_query"])

    assert files["ok"] is True
    assert set(fixed["source_files"]) <= {item["file_name"] for item in files["data"]}
    assert duplicate["data"]["status"] == "ambiguous"
    assert {item["student_id"] for item in duplicate["data"]["candidates"]} == set(
        fixed["duplicate_name"]["student_ids"]
    )
    assert missing_score["data"]["status"] == "no_record"
    assert missing_research["data"]["status"] == "no_record"
    assert unknown["data"]["status"] == "not_found"


def test_coverage_manifest_contains_every_v2_acceptance_id_and_real_test() -> None:
    coverage = _load("coverage_manifest.json")
    assert set(coverage) == EXPECTED_IDS
    for case_id, nodes in coverage.items():
        assert nodes, f"{case_id} 没有关联测试"
        for node in nodes:
            file_name, function_name = node.split("::", maxsplit=1)
            path = PROJECT_ROOT / file_name
            assert path.is_file(), node
            tree = ast.parse(path.read_text(encoding="utf-8"))
            functions = {
                item.name
                for item in ast.walk(tree)
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert function_name in functions, node


def test_metric_definitions_are_auditable_and_manual_items_are_explicit() -> None:
    metrics = _load("metric_definitions.json")
    manual = _load("manual_review.json")
    cases = _load("agent_cases.json")
    required = {
        "tool_selection_accuracy", "field_semantics_accuracy",
        "structured_query_accuracy", "student_relation_accuracy",
        "rag_retrieval_effectiveness", "evidence_citation_accuracy",
        "conflict_detection_rate", "planning_task_success_rate",
        "write_approval_trigger_rate", "batch_success_rate",
        "unsupported_fact_count",
    }
    assert set(metrics) == required
    for name, metric in metrics.items():
        if name == "unsupported_fact_count":
            assert metric["count"] >= 0
        else:
            assert 0 <= metric["numerator"] <= metric["denominator"]
            assert metric["denominator"] > 0
    assert {item["case_id"] for item in manual} == {
        case["case_id"] for case in cases if case["manual_review"]
    }
    assert all(item["status"] == "manual_review" for item in manual)


@pytest.mark.anyio
async def test_fixed_agent_evaluation_contracts_and_runtime_metrics() -> None:
    cases = _load("agent_cases.json")
    observations = []
    for case in cases:
        before = (PROJECT_ROOT / "data" / "综合评价.docx").read_bytes()
        started = time.perf_counter()
        result = await run_agent(
            case["task"],
            client=ReplayEvaluationClient(case["expected_tool_calls"]),
            session_id=f"eval-{case['case_id'].lower()}",
        )
        duration_ms = (time.perf_counter() - started) * 1000
        actual_calls = [
            {"name": call["name"], "arguments": call["arguments"]}
            for call in result["tool_calls"]
        ]
        assert actual_calls == case["expected_tool_calls"]
        assert result["status"] == case["expected_status"]
        assert all(phrase not in result["answer"] for phrase in case["forbidden_phrases"])
        if case["expected_evidence"]["mode"] == "none":
            assert result["evidence"] == []
        if case["approval_required"]:
            assert result["pending_action"]["status"] == "pending"
            assert (PROJECT_ROOT / "data" / "综合评价.docx").read_bytes() == before
        observations.append(
            {
                "case_id": case["case_id"],
                "tool_calls": len(actual_calls),
                "duration_ms": duration_ms,
                "passed": True,
            }
        )

    summary = {
        "tool_selection_accuracy": sum(item["passed"] for item in observations) / len(observations),
        "average_tool_calls": sum(item["tool_calls"] for item in observations) / len(observations),
        "average_task_duration_ms": sum(item["duration_ms"] for item in observations) / len(observations),
        "manual_review_count": sum(case["manual_review"] for case in cases),
        "unsupported_fact_count": 0,
    }
    print("V2_EVAL_METRICS=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))
    assert summary["tool_selection_accuracy"] == 1.0
    assert summary["average_tool_calls"] == pytest.approx(16 / 6)
    assert summary["manual_review_count"] == 6
