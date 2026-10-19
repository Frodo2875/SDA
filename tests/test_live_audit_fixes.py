"""Regressions for failures discovered with real model and browser requests."""

import json
from io import BytesIO

import pytest
from openpyxl import Workbook

from backend import agent, database
from backend.services.evidence_locator import locate_evidence
from backend.services.file_upload import save_uploaded_file
from backend.services.source_router import route_source
from backend.services.tool_scope_resolver import resolve_source_tool_scope
from backend.services.web_page_parser import parse_web_page
from backend.tools import excel_utils
from backend.tools.schema_tools import inspect_excel
from backend.tools.table_tools import aggregate_table


@pytest.mark.parametrize("query", [
    "只用本地材料，不联网，不查网页。",
    "根据本地材料生成报告，不要联网。",
    "请仅使用上传材料，并参考最新政策。",
    "不需要联网，读取 https://example.com/policy",
    "禁止访问网页，仅分析附件。",
    "Local files only; include the latest policy.",
    "Do not browse https://example.com/",
    "请你仅检索本地附件内的最新材料",
])
def test_explicit_source_restrictions_remove_web_tools(query):
    route = route_source(query)
    assert route["source_strategy"] == "LOCAL"
    assert route["urls"] == []
    scope = resolve_source_tool_scope(route, frozenset(agent.TOOL_FUNCTIONS))
    assert "retrieve_web" not in scope.allowed_tool_names
    _, result = agent._execute_tool(
        "retrieve_web", '{"query":"test"}', source_tool_scope=scope,
    )
    assert result["error_code"] == "TOOL_NOT_ALLOWED_FOR_SOURCE"


@pytest.mark.parametrize(("query", "expected"), [
    ("不要只用本地材料，请联网搜索", "BOTH"),
    ("结合本地材料和网页研究", "BOTH"),
    ("请联网查询最新政策", "WEB"),
    ("请读取 https://example.com/", "DIRECT_URL"),
])
def test_positive_source_requests_remain_available(query, expected):
    assert route_source(query)["source_strategy"] == expected


def test_real_world_meta_variants_do_not_crash_or_drop_body():
    document = parse_web_page(
        '<html><head><meta charset="utf-8"><meta http-equiv="X-UA-Compatible" '
        'content="IE=edge"/><meta name><meta property="og:title" content="Async I/O">'
        '<meta name="description" content="Python reference"></head>'
        '<body><p>asyncio supports concurrent I/O.</p><script>ignore this</script></body></html>',
        fetched_url="https://docs.python.org/3/library/asyncio.html",
    )
    assert document.title == "Async I/O"
    assert "asyncio supports concurrent I/O." in document.content
    assert "ignore this" not in document.content


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "物资"
    sheet.append(["编号", "类别", "数量"])
    sheet.append(["A1", "传感器", 17])
    sheet.append(["A2", "电池", 23])
    sheet.append(["A3", "传感器", 5])
    workbook.save(tmp_path / "库存.xlsx")
    database.initialize_database()
    file_id = database.get_file_record("库存.xlsx")["file_id"]
    assert inspect_excel(file_id)["ok"]
    return file_id


@pytest.mark.parametrize(("operation", "expected"), [
    ("sum", 22), ("avg", 11), ("min", 5), ("max", 17), ("count", 2),
])
def test_aggregation_exposes_real_contributors_and_computation(inventory, operation, expected):
    result = aggregate_table(
        inventory, "物资", operation, field="数量",
        filters=[{"field": "类别", "op": "=", "value": "传感器"}],
    )
    assert result["data"]["results"][0]["value"] == expected
    evidence = result["unified_evidence"]
    assert {item["cell"] for item in evidence} == {"B2", "C2", "B4", "C4"}
    assert {item["evidence_id"] for item in evidence} == set(
        result["data"]["aggregation"]["source_evidence_ids"]
    )
    assert {item["evidence_id"] for item in result["evidence_chain"]} == {
        item["evidence_id"] for item in evidence
    }
    for item in evidence:
        assert item["source_type"] == "LOCAL"
        location = locate_evidence(item["evidence_id"])
        assert location["ok"], location
    # The result 22 is a calculation over C2=17 and C4=5, never a fabricated cell.
    if operation == "sum":
        assert {item["value_summary"] for item in evidence if item["field"] == "数量"} == {"17", "5"}


def test_count_star_and_empty_matches_keep_truthful_provenance(inventory):
    count = aggregate_table(inventory, "物资", "count")
    assert count["data"]["results"][0]["value"] == 3
    assert {item["cell"] for item in count["unified_evidence"]} == {"A2", "A3", "A4"}
    empty = aggregate_table(inventory, "物资", "sum", field="数量", filters=[
        {"field": "类别", "op": "=", "value": "不存在"},
    ])
    assert empty["data"]["status"] == "not_found"
    assert empty["unified_evidence"] == []
    assert empty["data"]["aggregation"]["matched_rows"] == 0


@pytest.fixture
def scholarship_materials(tmp_path, monkeypatch):
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "uploads-test.db")
    database.initialize_database()
    for name, headers, values in [
        ("学生基本信息.xlsx", ["学号", "姓名", "专业", "年级", "班级"], ["S001", "合成甲", "计算机", "研一", "1班"]),
        ("学生成绩.xlsx", ["学号", "平均成绩", "专业排名"], ["S001", 92, 2]),
        ("科研成果.xlsx", ["学号", "论文数"], ["S001", 1]),
    ]:
        workbook = Workbook(); sheet = workbook.active
        sheet.append(headers); sheet.append(values)
        content = BytesIO(); workbook.save(content)
        assert save_uploaded_file(name, content.getvalue())["ok"]
    database.initialize_database()
    uploaded = save_uploaded_file(
        "奖学金评审办法.md",
        "# 评审办法\n\n一等奖学金：平均成绩不低于90分，专业排名在前3名，论文数不少于1篇。".encode(),
    )
    assert uploaded["ok"]


@pytest.mark.anyio
@pytest.mark.parametrize("wrong_rule_scope", [False, True])
async def test_cold_scholarship_workflow_discovers_schema_without_user_metadata(scholarship_materials, wrong_rule_scope):
    class DiscoveryClient:
        def __init__(self):
            self.round = 0
            self.files = {}

        async def create_chat_completion(self, messages, tools):
            advertised = {tool["function"]["name"] for tool in tools}
            assert {"list_files", "get_table_schema", "inspect_excel", "query_table"} <= advertised
            assert "get_student_scores" not in advertised
            self.round += 1
            if self.round == 1:
                calls = [("search_student", {"name_or_id": "S001"}), ("list_files", {})]
            else:
                for message in messages:
                    if message["role"] == "tool":
                        data = json.loads(message["content"]).get("data")
                        if isinstance(data, list) and data and "file_name" in data[0]:
                            self.files = {item["file_name"]: item["file_id"] for item in data}
                tables = [self.files[name] for name in ("学生成绩.xlsx", "科研成果.xlsx")]
                if self.round == 2:
                    calls = [("get_table_schema", {"file_id": file_id}) for file_id in tables]
                elif self.round == 3:
                    calls = [("inspect_excel", {"file_id": file_id}) for file_id in tables]
                elif self.round == 4:
                    calls = [("query_table", {"file_id": tables[0], "sheet": "Sheet", "filters": [{"field": "学号", "op": "=", "value": "S001"}], "select": ["平均成绩", "专业排名"]}),
                             ("query_table", {"file_id": tables[1], "sheet": "Sheet", "filters": [{"field": "学号", "op": "=", "value": "S001"}], "select": ["论文数"]}),
                             ("retrieve_document", {"scope": {"file_type": "txt"} if wrong_rule_scope else {"file_id": self.files["奖学金评审办法.md"]}, "query": "一等奖学金"})]
                elif wrong_rule_scope and self.round == 5:
                    calls = [("retrieve_document", {"scope": {"file_id": self.files["奖学金评审办法.md"]}, "query": "一等奖学金"})]
                elif self.round == (6 if wrong_rule_scope else 5):
                    calls = [("evaluate_scholarship_eligibility", {"student_id": "S001"})]
                else:
                    return {"content": "请采用规则工具的结论。"}
            return {"tool_calls": [{"id": f"{self.round}-{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}} for i, (name, args) in enumerate(calls)]}

    result = await agent.run_agent(
        "请判断S001是否符合一等奖学金资格。", session_id="cold-discovery", client=DiscoveryClient(),
    )
    assert result["status"] == "completed", result
    assert "满足一等奖学金条件" in result["answer"]
    assert not any(call["result"].get("error_code") == "TOOL_NOT_IN_PLAN" for call in result["tool_calls"])
    assert any(trace["tool_name"] == "inspect_excel" for trace in database.get_task_trace_records(result["task_id"]))
    assert all(step["status"] == "success" for step in database.get_task_step_records(result["task_id"]))


def test_markdown_scope_matches_the_supported_document_format(scholarship_materials):
    from backend.services.document_index import retrieve_document

    result = retrieve_document({"file_type": "markdown"}, "一等奖学金")
    assert result["ok"] and result["data"]["status"] == "found", result
    assert result["data"]["unified_evidence"]


@pytest.mark.parametrize(("status", "analysis_started", "can_retry"), [
    ("not_found", False, True), ("found", False, False), ("not_found", True, False),
])
def test_only_empty_retrieval_before_analysis_can_be_corrected(status, analysis_started, can_retry):
    from backend.runtime.planner import PlannedStep, StepType, TaskPlan
    from backend.runtime.task_runner import record_tool_execution, start_task, start_tool_step

    plan = TaskPlan("retrieval-correction", (
        PlannedStep(1, "检索规则", StepType.QUERY, "retrieve_document", step_id="rules"),
        PlannedStep(2, "规则判断", StepType.ANALYSIS, "evaluate", step_id="analysis", depends_on=("rules",)),
    ))
    task = start_task(session_id="correction", user_message="核验规则", plan=plan)
    step_id = start_tool_step(task_id=task["task_id"], tool_name="retrieve_document", arguments={"query": "first"})
    record_tool_execution(task_id=task["task_id"], step_id=step_id, tool_name="retrieve_document", arguments={"query": "first"}, result={"ok": True, "data": {"status": status}}, retry_count=0)
    if analysis_started:
        assert start_tool_step(task_id=task["task_id"], tool_name="evaluate", arguments={}) == "analysis"
    retried = start_tool_step(task_id=task["task_id"], tool_name="retrieve_document", arguments={"query": "corrected"})
    assert (retried == "rules") is can_retry


@pytest.mark.anyio
async def test_discovery_has_a_checkpointed_budget(scholarship_materials):
    class RepeatingClient:
        count = 0

        async def create_chat_completion(self, messages, tools):
            self.count += 1
            if self.count == 1:
                return {"tool_calls": [{"id": str(i), "type": "function", "function": {"name": "list_files", "arguments": "{}"}} for i in range(13)]}
            return {"content": "当前材料不足以判断。"}

    result = await agent.run_agent("请判断S001的奖学金资格", client=RepeatingClient(), max_tool_rounds=1)
    calls = [call for call in result["tool_calls"] if call["name"] == "list_files"]
    assert len(calls) == 13
    assert all(call["result"]["ok"] for call in calls[:12])
    assert calls[-1]["result"]["error_code"] == "PREPARATION_BUDGET_EXCEEDED"
