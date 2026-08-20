"""V3.1 regression tests for complete, traceable workflow execution."""

import json
import shutil
from pathlib import Path

import pytest

from backend import database
from backend.agent import run_agent
from backend.evidence import make_evidence_id
from backend.runtime.planner import StepType, create_plan
from backend.runtime.task_runner import (
    StepStatus,
    record_tool_execution,
    start_task,
    start_tool_step,
)
from backend.services.confirmation import cancel_action, confirm_action
from backend.tools import excel_utils
from backend.tools.schema_tools import inspect_excel


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMBINED_MESSAGE = (
    "根据S001的成绩、科研成果和奖学金办法判断是否符合一等奖学金，"
    "并生成综合评价写入综合评价.docx。"
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FinalAnswerClient:
    async def create_chat_completion(self, messages, tools):
        return {"role": "assistant", "content": "普通查询保持原路径。"}


class CombinedReplayClient:
    def __init__(self, materials: dict[str, str]) -> None:
        self.materials = materials
        self.round = 0

    async def create_chat_completion(self, messages, tools):
        self.round += 1
        if self.round == 1:
            return _calls(
                "identity",
                ("search_student", {"name_or_id": "S001"}),
                ("get_student_info", {"student_id": "S001"}),
            )
        if self.round == 2:
            return _calls(
                "sources",
                ("get_student_scores", {"student_id": "S001"}),
                ("get_student_research", {"student_id": "S001"}),
                (
                    "query_table",
                    {
                        "file_id": self.materials["score_id"],
                        "sheet": "Sheet",
                        "filters": [{"field": "学号", "op": "=", "value": "S001"}],
                        "select": ["平均分", "专业排名"],
                    },
                ),
                (
                    "query_table",
                    {
                        "file_id": self.materials["research_id"],
                        "sheet": "Sheet",
                        "filters": [{"field": "学号", "op": "=", "value": "S001"}],
                        "select": ["论文数", "专利数", "竞赛数"],
                    },
                ),
                (
                    "retrieve_document",
                    {
                        "scope": {"file_id": self.materials["rule_id"]},
                        "query": "一等奖学金评审办法",
                        "top_k": 5,
                    },
                ),
            )
        if self.round == 3:
            return _calls(
                "analysis",
                (
                    "evaluate_scholarship_eligibility",
                    {"student_id": "S001", "award_name": "一等奖学金"},
                ),
            )
        return {"role": "assistant", "content": "使用 Python 判断结果生成综合评价。"}


def _calls(prefix: str, *items: tuple[str, dict]) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"v3-{prefix}-{index}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }
            for index, (name, arguments) in enumerate(items)
        ],
    }


@pytest.fixture
def workflow_materials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    for name in ("学生基本信息.xlsx", "学生成绩.xlsx", "科研成果.xlsx", "综合评价.docx"):
        shutil.copy2(PROJECT_ROOT / "data" / name, tmp_path / name)
    database.initialize_database()

    score = database.get_file_record("学生成绩.xlsx")
    research = database.get_file_record("科研成果.xlsx")
    word = database.get_file_record("综合评价.docx")
    assert score and research and word
    assert inspect_excel(score["file_id"])["ok"] is True
    assert inspect_excel(research["file_id"])["ok"] is True

    uploads = tmp_path / "uploads"
    uploads.mkdir(exist_ok=True)
    rule_path = uploads / "V3奖学金评审办法.pdf"
    rule_path.write_bytes(b"isolated V3 workflow fixture")
    database.register_file(
        file_name=rule_path.name,
        file_type="pdf",
        file_path=f"data/uploads/{rule_path.name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
        index_status="indexed",
    )
    rule = database.get_file_record(rule_path.name)
    assert rule
    text = "一等奖学金：平均成绩不低于90分，专业排名在前3名，论文数不少于1篇。"
    chunk_id = make_evidence_id(file_id=rule["file_id"], index=0, text=text)
    database.replace_document_chunks(
        rule["file_id"],
        [
            {
                "chunk_id": chunk_id,
                "file_id": rule["file_id"],
                "page_no": 1,
                "chunk_index": 0,
                "chunk_text": text,
                "text_hash": make_evidence_id(text=text),
                "metadata": {"source_type": "pdf", "page_no": 1},
            }
        ],
    )
    return {
        "score_id": score["file_id"],
        "research_id": research["file_id"],
        "rule_id": rule["file_id"],
        "word_path": str(tmp_path / "综合评价.docx"),
    }


@pytest.mark.anyio
async def test_case1_simple_query_keeps_v2_lightweight_path() -> None:
    assert create_plan("你好，请简单回答。") is None
    result = await run_agent(
        "你好，请简单回答。", client=FinalAnswerClient(), session_id="v3-simple"
    )
    assert result["status"] == "completed"
    assert "task_id" not in result


def test_case2_scholarship_plan_has_executable_step_contract_and_statuses() -> None:
    plan = create_plan("根据S001材料判断是否满足一等奖学金条件")
    assert plan is not None
    assert len(plan.steps) == 6
    assert {step.step_type for step in plan.steps} == {
        StepType.QUERY, StepType.ANALYSIS, StepType.GENERATE
    }
    assert all(
        set(step.as_dict()) >= {
            "step_id", "step_type", "tool_name", "description", "input", "expected_output"
        }
        for step in plan.steps
    )

    task = start_task(session_id="v3-status", user_message="奖学金判断", plan=plan)
    first = database.get_task_step_records(task["task_id"])[0]
    assert first["status"] == StepStatus.CREATED.value
    assert set(first) >= {
        "step_id", "step_type", "tool_name", "description", "input", "expected_output"
    }
    step_id = start_tool_step(
        task_id=task["task_id"], tool_name="search_student", arguments={"name_or_id": "S001"}
    )
    assert step_id == first["step_id"]
    assert database.get_task_step_records(task["task_id"])[0]["status"] == StepStatus.RUNNING.value
    record_tool_execution(
        task_id=task["task_id"], step_id=step_id, tool_name="search_student",
        arguments={"name_or_id": "S001"},
        result={"ok": True, "data": {"status": "found"}, "error_code": None, "message": "成功"},
        retry_count=0,
    )
    assert database.get_task_step_records(task["task_id"])[0]["status"] == StepStatus.SUCCESS.value


@pytest.mark.anyio
async def test_case3_combined_workflow_confirm_cancel_and_trace(
    workflow_materials: dict[str, str],
) -> None:
    plan = create_plan(COMBINED_MESSAGE)
    assert plan is not None
    assert plan.task_type == "scholarship_evaluation_and_word_write"
    assert len(plan.steps) == 12
    assert {step.step_type for step in plan.steps} == set(StepType)

    word_path = Path(workflow_materials["word_path"])
    original = word_path.read_bytes()
    cancelled_run = await run_agent(
        COMBINED_MESSAGE,
        client=CombinedReplayClient(workflow_materials),
        session_id="v3-combined-cancel",
    )
    cancelled_steps = database.get_task_step_records(cancelled_run["task_id"])
    assert cancelled_steps[-2]["status"] == StepStatus.WAITING_CONFIRMATION.value
    assert cancel_action(cancelled_run["pending_action"]["action_id"])["ok"] is True
    assert word_path.read_bytes() == original
    cancelled_steps = database.get_task_step_records(cancelled_run["task_id"])
    assert [step["status"] for step in cancelled_steps[-2:]] == [
        StepStatus.CANCELLED.value, StepStatus.CANCELLED.value
    ]

    confirmed_run = await run_agent(
        COMBINED_MESSAGE,
        client=CombinedReplayClient(workflow_materials),
        session_id="v3-combined-confirm",
    )
    assert confirm_action(confirmed_run["pending_action"]["action_id"])["ok"] is True
    assert word_path.read_bytes() != original
    confirmed_steps = database.get_task_step_records(confirmed_run["task_id"])
    assert [step["status"] for step in confirmed_steps] == [StepStatus.SUCCESS.value] * 12

    traces = database.get_task_trace_records(confirmed_run["task_id"])
    assert {trace["event_type"] for trace in traces} >= {
        "step_started", "tool_execution", "step_waiting_confirmation", "step_confirmed"
    }
    assert all(trace["task_id"] == confirmed_run["task_id"] for trace in traces)
    assert all(trace["step_id"] and trace["tool_name"] for trace in traces)
