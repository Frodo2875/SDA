"""Final Demo B: reproducible end-to-end validation without a live LLM or .env."""

import json
import shutil
from pathlib import Path

import pytest

from backend import agent, database
from backend.agent import run_agent
from backend.evidence import make_evidence_id
from backend.services.confirmation import (
    cancel_action,
    confirm_action,
    create_pending_action,
    create_pending_undo_action,
    rollback_file,
)
from backend.tools import excel_utils
from backend.tools.schema_tools import inspect_excel


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_MESSAGE = (
    "根据S001张三的成绩、科研成果和奖学金办法，判断他是否符合一等奖学金，"
    "并生成综合评价写入综合评价.docx。"
)


@pytest.fixture
def demo_b_materials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
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
    rule_path = uploads / "奖学金评审办法.pdf"
    rule_path.write_bytes(b"isolated demo fixture; parsing is covered by R01")
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
    rule_text = "一等奖学金：平均成绩不低于90分，专业排名在前3名，论文数不少于1篇。"
    chunk_id = make_evidence_id(file_id=rule["file_id"], index=0, text=rule_text)
    database.replace_document_chunks(
        rule["file_id"],
        [
            {
                "chunk_id": chunk_id,
                "file_id": rule["file_id"],
                "page_no": 2,
                "chunk_index": 0,
                "chunk_text": rule_text,
                "text_hash": make_evidence_id(text=rule_text),
                "metadata": {"source_type": "pdf", "page_no": 2},
            }
        ],
    )
    return {
        "score_id": score["file_id"],
        "research_id": research["file_id"],
        "rule_id": rule["file_id"],
        "word_id": word["file_id"],
        "word_path": tmp_path / "综合评价.docx",
        "chunk_id": chunk_id,
    }


class AmbiguousReplayClient:
    async def create_chat_completion(self, messages, tools):
        return _assistant_calls(
            [("search_student", {"name_or_id": "张三"})], "ambiguous"
        )


class DemoBReplayClient:
    def __init__(self, materials: dict[str, object]) -> None:
        self.materials = materials
        self.round = 0

    async def create_chat_completion(self, messages, tools):
        self.round += 1
        if self.round == 1:
            return _assistant_calls(
                [
                    ("search_student", {"name_or_id": "S001"}),
                    ("get_student_info", {"student_id": "S001"}),
                ],
                "identity",
            )
        if self.round == 2:
            return _assistant_calls(
                [
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
                ],
                "materials",
            )
        if self.round == 3:
            return _assistant_calls(
                [
                    (
                        "evaluate_scholarship_eligibility",
                        {"student_id": "S001", "award_name": "一等奖学金"},
                    )
                ],
                "evaluation",
            )
        return {
            "role": "assistant",
            "content": "模型草稿不得覆盖 Python 的规则判断结论。",
            "tool_calls": [],
        }


def _assistant_calls(items: list[tuple[str, dict]], prefix: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"demo-b-{prefix}-{index}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
            for index, (name, arguments) in enumerate(items)
        ],
    }


@pytest.mark.anyio
async def test_demo_b_identity_safety_and_full_version_chain(
    demo_b_materials: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    ambiguous = await run_agent(
        "根据张三的成绩、科研成果和奖学金办法，判断他是否符合一等奖学金，"
        "并生成综合评价写入综合评价.docx。",
        client=AmbiguousReplayClient(),
        session_id="demo-b-main",
    )
    assert ambiguous["status"] == "clarification_required"
    assert "S001" in ambiguous["answer"] and "S004" in ambiguous["answer"]
    assert [call["name"] for call in ambiguous["tool_calls"]] == ["search_student"]

    word_path = demo_b_materials["word_path"]
    original = word_path.read_bytes()
    original_retriever = agent.TOOL_FUNCTIONS["retrieve_document"]
    attempts = 0

    def fail_once_then_retrieve(**arguments):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("isolated transient read failure")
        return original_retriever(**arguments)

    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "retrieve_document", fail_once_then_retrieve)
    result = await run_agent(
        DEMO_MESSAGE,
        client=DemoBReplayClient(demo_b_materials),
        session_id="demo-b-main",
    )

    assert result["status"] == "confirmation_required", json.dumps(result, ensure_ascii=False)
    assert word_path.read_bytes() == original
    assert [call["name"] for call in result["tool_calls"]] == [
        "search_student",
        "get_student_info",
        "get_student_scores",
        "get_student_research",
        "query_table",
        "query_table",
        "retrieve_document",
        "evaluate_scholarship_eligibility",
    ]
    assert next(call for call in result["tool_calls"] if call["name"] == "retrieve_document")["retry_count"] == 1
    evaluation = result["tool_calls"][-1]["result"]["data"]
    assert evaluation["conclusion"] == "eligible"
    assert "满足一等奖学金条件" in evaluation["answer"]
    sources = {item["file_name"] for item in evaluation["evidence_chain"]}
    assert sources == {"学生成绩.xlsx", "科研成果.xlsx", "奖学金评审办法.pdf"}
    pdf_evidence = next(
        item for item in evaluation["evidence_chain"] if item["file_name"].endswith(".pdf")
    )
    assert pdf_evidence["page_no"] == 2
    assert pdf_evidence["chunk_id"] == demo_b_materials["chunk_id"]

    action = result["pending_action"]
    assert action["diff_preview"]["operation_type"] == "append"
    assert action["diff_preview"]["after"].endswith(action["content"])
    task = database.get_task_record(result["task_id"])
    assert task["status"] == "waiting_confirmation"
    calls_before_confirm = len(result["tool_calls"])

    confirmed = confirm_action(action["action_id"])
    assert confirmed["ok"] is True
    assert len(result["tool_calls"]) == calls_before_confirm
    assert word_path.read_bytes() != original
    write_result = confirmed["data"]["write_result"]["data"]
    new_version_id = write_result["new_version"]["version_id"]
    versions_after_write = database.list_file_versions(demo_b_materials["word_id"])
    assert len(versions_after_write) == 2
    assert versions_after_write[-1]["version_id"] == new_version_id

    bytes_after_write = word_path.read_bytes()
    repeated = confirm_action(action["action_id"])
    assert repeated["ok"] is False
    assert repeated["error_code"] == "ACTION_NOT_PENDING"
    assert word_path.read_bytes() == bytes_after_write
    assert len(database.list_file_versions(demo_b_materials["word_id"])) == 2

    undo = create_pending_undo_action(
        session_id="demo-b-undo", file_id=demo_b_materials["word_id"]
    )
    assert undo["ok"] is True
    assert word_path.read_bytes() == bytes_after_write
    assert confirm_action(undo["data"]["action_id"])["ok"] is True
    assert word_path.read_bytes() == original
    assert len(database.list_file_versions(demo_b_materials["word_id"])) == 3

    rollback = rollback_file(
        demo_b_materials["word_id"], new_version_id, session_id="demo-b-rollback"
    )
    assert rollback["ok"] is True
    assert word_path.read_bytes() == original
    assert confirm_action(rollback["data"]["action_id"])["ok"] is True
    assert word_path.read_bytes() == bytes_after_write
    history = database.list_file_versions(demo_b_materials["word_id"])
    assert len(history) == 4
    assert history[-1]["change_type"] == "rollback"
    assert len({item["version_id"] for item in history}) == 4

    cancelled = create_pending_action(
        session_id="demo-b-cancel",
        target_file="综合评价.docx",
        student_id="S001",
        student_name="张三",
        content="这段取消内容绝不能写入。",
    )
    before_cancel = word_path.read_bytes()
    assert cancel_action(cancelled["data"]["action_id"])["ok"] is True
    assert word_path.read_bytes() == before_cancel

    traces = database.get_task_trace_records(result["task_id"])
    completed_tools = [
        item["tool_name"] for item in traces if item["event_type"] == "tool_execution"
    ]
    assert completed_tools == [
        "search_student",
        "get_student_info",
        "get_student_scores",
        "get_student_research",
        "query_table",
        "query_table",
        "retrieve_document",
        "evaluate_scholarship_eligibility",
        "preview_word_diff",
        "write_word",
    ]
    assert next(
        item for item in traces
        if item["tool_name"] == "retrieve_document"
        and item["event_type"] == "tool_execution"
    )["retry_count"] == 1


@pytest.mark.anyio
async def test_demo_b_combined_plan_covers_the_full_workflow(
    demo_b_materials: dict[str, object]
) -> None:
    """The combined Plan covers every query, analysis, HITL, and write step."""
    result = await run_agent(
        DEMO_MESSAGE,
        client=DemoBReplayClient(demo_b_materials),
        session_id="demo-b-plan-boundary",
    )
    steps = database.get_task_step_records(result["task_id"])
    traces = database.get_task_trace_records(result["task_id"])

    assert [step["step_name"] for step in steps] == [
        "确认学生身份",
        "读取学生基本信息",
        "读取学生成绩",
        "读取学生科研",
        "查询学生成绩",
        "查询科研成果",
        "检索奖学金评审办法",
        "执行奖学金规则判断",
        "生成待写入内容",
        "生成 Word Diff 预览",
        "等待用户确认",
        "执行确认写入",
    ]
    assert result["status"] == "confirmation_required", json.dumps(result, ensure_ascii=False)
    assert database.get_task_record(result["task_id"])["status"] == "waiting_confirmation"
    assert "preview_word_diff" in {item["tool_name"] for item in traces}
    assert any(step["step_type"] == "CONFIRM" for step in steps)
    assert any(step["step_type"] == "WRITE" for step in steps)
    assert all(item["task_id"] == result["task_id"] for item in traces)
    assert all(item["step_id"] for item in traces)
