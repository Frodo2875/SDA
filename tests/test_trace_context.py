"""V2.11 E05/E06 Trace, Evidence, redaction and bounded Context tests."""

import json

import pytest

from backend import agent as agent_module
from backend import database
from backend.agent import run_agent
from backend.runtime.context_manager import (
    MAX_CONTEXT_PROMPT_CHARS,
    build_context_prompt,
    get_context,
    save_context,
)
from backend.services.redaction import redact_text


pytestmark = pytest.mark.anyio


def _call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


class PhoneTraceClient:
    def __init__(self) -> None:
        self.round = 0

    async def create_chat_completion(self, messages, tools):
        self.round += 1
        if self.round == 1:
            return {
                "role": "assistant", "content": None,
                "tool_calls": [_call("phone", "search_student", {"name_or_id": "13812345678"})],
            }
        return {"role": "assistant", "content": "当前材料中没有匹配学生。", "tool_calls": []}


class StudentContextClient:
    def __init__(self, *, research: bool = False) -> None:
        self.round = 0
        self.research = research
        self.first_user_message = ""

    async def create_chat_completion(self, messages, tools):
        self.round += 1
        if self.round == 1:
            self.first_user_message = next(
                item["content"] for item in reversed(messages) if item["role"] == "user"
            )
            if self.research:
                return {
                    "role": "assistant", "content": None,
                    "tool_calls": [_call("research", "get_student_research", {"student_id": "S001"})],
                }
            return {
                "role": "assistant", "content": None,
                "tool_calls": [
                    _call("identity", "search_student", {"name_or_id": "S001"}),
                    _call("scores", "get_student_scores", {"student_id": "S001"}),
                ],
            }
        return {"role": "assistant", "content": "查询完成。", "tool_calls": []}


class AmbiguousClient:
    async def create_chat_completion(self, messages, tools):
        return {
            "role": "assistant", "content": None,
            "tool_calls": [_call("ambiguous", "search_student", {"name_or_id": "张三"})],
        }


class BombClient:
    async def create_chat_completion(self, messages, tools):
        raise AssertionError("重名 Context 必须在调用 LLM 前停止")


class EvidenceClient:
    def __init__(self, file_id: str = "a" * 32) -> None:
        self.round = 0
        self.file_id = file_id

    async def create_chat_completion(self, messages, tools):
        self.round += 1
        if self.round == 1:
            return {
                "role": "assistant", "content": None,
                "tool_calls": [_call("evidence", "query_table", {
                    "file_id": self.file_id, "sheet": "学生", "filters": [],
                    "select": ["姓名"], "limit": 1,
                })],
            }
        return {"role": "assistant", "content": "依据材料完成回答。", "tool_calls": []}


async def test_e05_trace_records_observable_fields_and_redacts_phone() -> None:
    phone = "13812345678"
    result = await run_agent("查询手机号对应学生", client=PhoneTraceClient(), session_id="e05")
    traces = database.get_session_trace_records("e05")

    assert result["status"] == "completed"
    assert len(traces) == 2
    safety_trace, trace = traces
    assert safety_trace["event_type"] == "safety_policy_check"
    assert safety_trace["result_status"] == "allow"
    assert trace["event_type"] == "tool_execution"
    assert trace["tool_name"] == "search_student"
    assert trace["result_status"] == "success"
    assert trace["duration_ms"] >= 0
    assert trace["retry_count"] == 0
    assert phone not in json.dumps(trace, ensure_ascii=False)
    assert "138****5678" in trace["arguments_summary"]
    assert phone not in json.dumps(safety_trace, ensure_ascii=False)
    assistant = database.fetch_all("chat_messages")[-1]
    assert phone not in assistant["used_tools"]


async def test_e06_agent_returns_real_evidence_and_context_keeps_reference_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database.register_file(
        file_name="上传表.xlsx",
        file_type="excel",
        file_path="data/uploads/上传表.xlsx",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
    )
    file_id = database.get_file_record("上传表.xlsx")["file_id"]
    evidence = {
        "evidence_id": "ev-001", "source_type": "structured",
        "file_id": file_id, "file_name": "上传表.xlsx", "sheet": "学生",
        "page_no": None, "chunk_id": None, "field": "姓名",
        "record_key": "S001", "value_summary": "张三",
    }
    monkeypatch.setitem(
        agent_module.TOOL_FUNCTIONS,
        "query_table",
        lambda **kwargs: {
            "ok": True, "data": {"status": "found", "rows": [{"姓名": "张三"}]},
            "error_code": None, "message": "找到记录", "evidence_chain": [evidence],
        },
    )

    result = await run_agent(
        "查询上传表", client=EvidenceClient(file_id), session_id="e06"
    )
    context = get_context("e06")

    assert result["evidence"] == [evidence]
    assert context["current_file"] == {"file_id": file_id, "file_name": "上传表.xlsx"}
    assert context["evidence_refs"] == [
        {key: evidence[key] for key in (
            "evidence_id", "source_type", "file_id", "file_name", "sheet",
            "field", "record_key",
        )}
    ]
    assert "value_summary" not in context["evidence_refs"][0]


async def test_unique_student_context_resolves_follow_up_pronoun() -> None:
    first = StudentContextClient()
    first_result = await run_agent("查询S001的成绩", client=first, session_id="context-unique")
    second = StudentContextClient(research=True)
    second_result = await run_agent("那他的科研呢？", client=second, session_id="context-unique")

    assert first_result["status"] == "completed"
    assert second_result["status"] == "completed"
    assert "S001" in second.first_user_message
    assert second_result["tool_calls"][0]["name"] == "get_student_research"
    assert get_context("context-unique")["current_student"]["student_id"] == "S001"


async def test_ambiguous_context_requires_confirmation_before_pronoun() -> None:
    first = await run_agent("查询张三", client=AmbiguousClient(), session_id="context-ambiguous")
    second = await run_agent("那他的科研呢？", client=BombClient(), session_id="context-ambiguous")

    assert first["status"] == "clarification_required"
    assert second["status"] == "clarification_required"
    assert "请先确认学号" in second["answer"]
    assert {item["student_id"] for item in get_context("context-ambiguous")["ambiguity_candidates"]} == {"S001", "S004"}


async def test_context_prompt_is_bounded_and_does_not_include_full_history() -> None:
    state = get_context("context-bounded")
    state["successful_steps"] = [
        {"sequence": index, "step_name": "步骤", "result_summary": "结果" * 500}
        for index in range(100)
    ]
    state["evidence_refs"] = [
        {"evidence_id": str(index), "file_name": "材料.xlsx", "field": "字段"}
        for index in range(100)
    ]
    state["session_summary"] = "摘要" * 2000
    save_context(state)
    stored = get_context("context-bounded")
    prompt = build_context_prompt(stored)

    assert len(stored["successful_steps"]) == 12
    assert len(stored["evidence_refs"]) == 20
    assert len(prompt) <= MAX_CONTEXT_PROMPT_CHARS + 100
    assert "全部 chat" not in prompt


async def test_basic_sensitive_types_are_redacted() -> None:
    original = (
        "手机13812345678 邮箱zhangsan@example.com "
        "身份证110101199001011234 住址北京市海淀区中关村大街1号"
    )
    redacted = redact_text(original)

    for sensitive in (
        "13812345678", "zhangsan@example.com", "110101199001011234",
        "中关村大街1号",
    ):
        assert sensitive not in redacted
    assert "138****5678" in redacted
    assert "z***@example.com" in redacted
