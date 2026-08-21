"""V2.7 R03/E01-E04 hybrid scholarship evidence-chain integration tests."""

import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from backend import agent, database
from backend.agent import run_agent
from backend.evidence import make_evidence_id
from backend.services.document_index import retrieve_document
from backend.tools import excel_utils
from backend.tools.schema_tools import inspect_excel
from backend.tools.student_tools import search_student
from backend.tools.table_tools import query_table


FORMAL_EVIDENCE_FIELDS = {
    "evidence_id",
    "task_id",
    "source_type",
    "file_id",
    "file_name",
    "sheet",
    "page_no",
    "chunk_id",
    "field",
    "record_key",
    "value_summary",
}
EVIDENCE_2_LOCATOR_FIELDS = {"block_id", "table", "cell", "bbox", "confidence"}


@pytest.fixture
def hybrid_materials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    _workbook(
        tmp_path / "学生基本信息.xlsx",
        ["学号", "姓名", "专业", "年级", "班级"],
        [["S001", "张三", "计算机科学", "研一", "1班"]],
    )
    _workbook(
        tmp_path / "学生成绩.xlsx",
        ["学号", "平均成绩", "专业排名", "英语"],
        [["S001", 92, 2, 88], ["S002", 85, 8, 90]],
    )
    _workbook(
        tmp_path / "科研成果.xlsx",
        ["学号", "论文数", "专利数", "竞赛数"],
        [["S001", 1, 0, 2]],
    )
    database.initialize_database()
    score = database.get_file_record("学生成绩.xlsx")
    research = database.get_file_record("科研成果.xlsx")
    assert inspect_excel(score["file_id"])["ok"] is True
    assert inspect_excel(research["file_id"])["ok"] is True

    uploads = tmp_path / "uploads"
    uploads.mkdir(exist_ok=True)
    rule_path = uploads / "奖学金评审办法.pdf"
    rule_path.write_bytes(b"pdf fixture")
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
    _set_chunks(
        rule["file_id"],
        [
            "一等奖学金：平均成绩不低于90分，专业排名在前3名，论文数不少于1篇。",
            "二等奖学金：平均成绩不低于80分。",
        ],
    )

    unused_path = uploads / "未使用的住宿办法.pdf"
    unused_path.write_bytes(b"pdf fixture")
    database.register_file(
        file_name=unused_path.name,
        file_type="pdf",
        file_path=f"data/uploads/{unused_path.name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
        index_status="indexed",
    )
    unused = database.get_file_record(unused_path.name)
    _set_chunks(unused["file_id"], ["奖学金申请期间宿舍门禁时间保持不变。"])
    return {
        "score_id": score["file_id"],
        "research_id": research["file_id"],
        "rule_id": rule["file_id"],
        "unused_id": unused["file_id"],
        "sheet": "Sheet",
    }


def _workbook(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet"
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
    workbook.close()


def _set_chunks(file_id: str, texts: list[str]) -> None:
    chunks = []
    for index, text in enumerate(texts):
        chunk_id = make_evidence_id(file_id=file_id, index=index, text=text)
        chunks.append(
            {
                "chunk_id": chunk_id,
                "file_id": file_id,
                "page_no": index + 1,
                "chunk_index": index,
                "chunk_text": text,
                "text_hash": make_evidence_id(text=text),
                "metadata": {"source_type": "pdf", "page_no": index + 1},
            }
        )
    database.replace_document_chunks(file_id, chunks)


def _calls(materials: dict[str, str], *, include_research: bool = True, query: str = "一等奖学金") -> list[dict]:
    identity = search_student("S001")
    score = query_table(
        materials["score_id"],
        materials["sheet"],
        filters=[{"field": "学号", "op": "=", "value": "S001"}],
        select=["平均成绩", "专业排名", "英语"],
    )
    calls = [
        {"name": "search_student", "arguments": {"name_or_id": "S001"}, "result": identity},
        {
            "name": "query_table",
            "arguments": {
                "file_id": materials["score_id"],
                "sheet": materials["sheet"],
                "filters": [{"field": "学号", "op": "=", "value": "S001"}],
                "select": ["平均成绩", "专业排名", "英语"],
                "order_by": None,
                "limit": None,
            },
            "result": score,
        },
    ]
    if include_research:
        research = query_table(
            materials["research_id"],
            materials["sheet"],
            filters=[{"field": "学号", "op": "=", "value": "S001"}],
            select=["论文数", "专利数", "竞赛数"],
        )
        calls.append(
            {
                "name": "query_table",
                "arguments": {
                    "file_id": materials["research_id"],
                    "sheet": materials["sheet"],
                    "filters": [{"field": "学号", "op": "=", "value": "S001"}],
                    "select": ["论文数", "专利数", "竞赛数"],
                    "order_by": None,
                    "limit": None,
                },
                "result": research,
            }
        )
    retrieved = retrieve_document({}, query, top_k=5)
    calls.append(
        {
            "name": "retrieve_document",
            "arguments": {"scope": {}, "query": query, "top_k": 5},
            "result": retrieved,
        }
    )
    return calls


def test_r03_complete_hybrid_chain_is_evaluated_by_python(hybrid_materials) -> None:
    calls = _calls(hybrid_materials)

    arguments, result = agent._execute_tool(
        "evaluate_scholarship_eligibility",
        json.dumps({"student_id": "S001", "award_name": "一等奖学金"}, ensure_ascii=False),
        calls,
    )

    assert arguments == {"student_id": "S001", "award_name": "一等奖学金"}
    assert result["ok"] is True
    assert result["data"]["conclusion"] == "eligible"
    conditions = {item["field"]: item for item in result["data"]["conditions"]}
    assert conditions["average_score"]["actual_value"] == 92
    assert conditions["average_score"]["threshold"] == 90
    assert conditions["rank"]["actual_value"] == 2
    assert conditions["papers"]["actual_value"] == 1
    assert all(item["passed"] for item in conditions.values())


def test_e01_formal_evidence_chain_contains_only_values_used_for_conclusion(hybrid_materials) -> None:
    calls = _calls(hybrid_materials)
    _, result = agent._execute_tool(
        "evaluate_scholarship_eligibility",
        '{"student_id":"S001","award_name":"一等奖学金"}',
        calls,
    )

    chain = result["data"]["evidence_chain"]
    assert chain
    assert all(FORMAL_EVIDENCE_FIELDS <= set(item) for item in chain)
    assert all(
        set(item) <= FORMAL_EVIDENCE_FIELDS | EVIDENCE_2_LOCATOR_FIELDS
        for item in chain
    )
    structured = [item for item in chain if item["source_type"] == "structured"]
    assert all({"table", "cell", "confidence"} <= set(item) for item in structured)
    assert {item["source_type"] for item in chain} == {"structured", "unstructured"}
    assert "英语" not in {item["field"] for item in chain}
    assert "未使用的住宿办法.pdf" not in {item["file_name"] for item in chain}
    assert {item["field"] for item in chain if item["source_type"] == "structured"} == {
        "平均成绩",
        "专业排名",
        "论文数",
    }
    answer = result["data"]["answer"]
    assert "未使用的住宿办法.pdf" not in answer
    assert "学生成绩.xlsx" in answer
    assert "科研成果.xlsx" in answer
    assert "奖学金评审办法.pdf" in answer


def test_e02_missing_research_attempt_returns_insufficient_not_definite(hybrid_materials) -> None:
    calls = _calls(hybrid_materials)
    research_call = next(
        call
        for call in calls
        if call["name"] == "query_table" and "论文数" in call["arguments"].get("select", [])
    )
    research_call["result"] = {
        **research_call["result"],
        "data": {"status": "not_found", "rows": [], "matched_rows": 0, "returned_rows": 0},
        "evidence_chain": [],
    }

    _, result = agent._execute_tool(
        "evaluate_scholarship_eligibility",
        '{"student_id":"S001","award_name":"一等奖学金"}',
        calls,
    )

    assert result["ok"] is True
    assert result["data"]["conclusion"] == "insufficient_evidence"
    assert result["data"]["conclusion_text"] == "当前材料不足以判断。"
    assert any("科研" in item for item in result["data"]["missing"])
    assert "满足一等奖学金条件" not in result["data"]["answer"]
    assert "不满足一等奖学金条件" not in result["data"]["answer"]


def test_e03_missing_or_ambiguous_rules_never_produce_a_definite_result(hybrid_materials) -> None:
    missing_calls = _calls(hybrid_materials, query="材料中不存在的博士专项规则")
    _, missing = agent._execute_tool(
        "evaluate_scholarship_eligibility",
        '{"student_id":"S001","award_name":"博士专项奖学金"}',
        missing_calls,
    )
    _set_chunks(
        hybrid_materials["rule_id"],
        ["一等奖学金：平均成绩不低于90分，或论文数不少于1篇。"],
    )
    ambiguous_calls = _calls(hybrid_materials)
    _, ambiguous = agent._execute_tool(
        "evaluate_scholarship_eligibility",
        '{"student_id":"S001","award_name":"一等奖学金"}',
        ambiguous_calls,
    )

    assert missing["data"]["conclusion"] == "insufficient_evidence"
    assert "规则" in missing["data"]["missing"]
    assert ambiguous["data"]["conclusion"] == "insufficient_evidence"
    assert any("规则存在冲突" in item for item in ambiguous["data"]["missing"])


def test_e04_model_cannot_inject_values_or_skip_required_tools(hybrid_materials) -> None:
    calls = _calls(hybrid_materials, include_research=False)
    _, sequence_error = agent._execute_tool(
        "evaluate_scholarship_eligibility",
        '{"student_id":"S001","award_name":"一等奖学金"}',
        calls,
    )
    arguments, injection = agent._execute_tool(
        "evaluate_scholarship_eligibility",
        '{"student_id":"S001","award_name":"一等奖学金","average_score":100,"threshold":0}',
        _calls(hybrid_materials),
    )

    assert sequence_error["error_code"] == "TOOL_SEQUENCE_ERROR"
    assert injection["error_code"] == "INVALID_TOOL_ARGUMENTS"
    assert arguments["average_score"] == 100


class ScholarshipClient:
    def __init__(self, materials: dict[str, str]) -> None:
        self.materials = materials
        self.round = 0

    async def create_chat_completion(self, messages, tools):
        self.round += 1
        if self.round == 1:
            return _assistant_calls([("search_student", {"name_or_id": "S001"})], self.round)
        if self.round == 2:
            return _assistant_calls(
                [
                    (
                        "query_table",
                        {
                            "file_id": self.materials["score_id"],
                            "sheet": "Sheet",
                            "filters": [{"field": "学号", "op": "=", "value": "S001"}],
                            "select": ["平均成绩", "专业排名"],
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
                            "query": "一等奖学金",
                            "top_k": 5,
                        },
                    ),
                ],
                self.round,
            )
        if self.round == 3:
            return _assistant_calls(
                [("evaluate_scholarship_eligibility", {"student_id": "S001", "award_name": "一等奖学金"})],
                self.round,
            )
        return {"role": "assistant", "content": "模型声称该学生不符合，并引用了未调用的虚构文件。", "tool_calls": []}


def _assistant_calls(items: list[tuple[str, dict]], round_no: int) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"hybrid-{round_no}-{index}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }
            for index, (name, arguments) in enumerate(items)
        ],
    }


@pytest.mark.anyio
async def test_complete_scholarship_agent_integration_uses_python_conclusion(hybrid_materials) -> None:
    result = await run_agent(
        "根据S001的成绩、科研成果和奖学金评审办法，判断他是否满足一等奖学金条件并说明依据。",
        client=ScholarshipClient(hybrid_materials),
        session_id="hybrid-integration",
    )

    assert result["status"] == "completed"
    assert [call["name"] for call in result["tool_calls"]] == [
        "search_student",
        "query_table",
        "query_table",
        "retrieve_document",
        "evaluate_scholarship_eligibility",
    ]
    assert "满足一等奖学金条件" in result["answer"]
    assert "虚构文件" not in result["answer"]
    assert "本次结论使用了" in result["answer"]
