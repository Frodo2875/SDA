"""Tests for the LLM tool-calling loop using a scripted model and real tools."""

import json
from typing import Any

import pytest

from backend import agent as agent_module
from backend.agent import TOOL_DEFINITIONS, run_agent


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


SCENARIOS = {
    "查询S001的学生信息": ("get_student_info", {"student_id": "S001"}),
    "现在知识库有哪些文件？": ("list_files", {}),
    "查询一个不存在的赵六": ("search_student", {"name_or_id": "赵六"}),
}

EXPECTED_TOOL_NAMES = {
    "get_top_three_students",
    "list_files",
    "search_student",
    "get_student_info",
    "get_student_scores",
    "get_student_research",
    "compare_students",
    "inspect_excel",
    "get_table_schema",
    "query_table",
    "aggregate_table",
    "find_cross_file_conflicts",
    "validate_document",
    "find_duplicate_records",
    "validate_student_data",
    "retrieve_document",
}


class ScenarioClient:
    """Script model tool choices while deriving final answers from tool messages."""

    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []

    async def create_chat_completion(self, messages, tools):
        self.requests.append(messages.copy())
        assert {item["function"]["name"] for item in tools} == EXPECTED_TOOL_NAMES
        assert "write_word" not in {item["function"]["name"] for item in tools}

        if messages[-1]["role"] == "user":
            name, arguments = SCENARIOS[messages[-1]["content"]]
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_test_1",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                ],
            }

        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "call_test_1"
        tool_result = json.loads(messages[-1]["content"])
        return {
            "role": "assistant",
            "content": f"依据工具结果：{json.dumps(tool_result['data'], ensure_ascii=False)}",
        }


@pytest.mark.parametrize(("question", "expected_tool"), [(key, value[0]) for key, value in SCENARIOS.items()])
async def test_first_batch_questions_use_real_tool_results(question: str, expected_tool: str) -> None:
    result = await run_agent(question, client=ScenarioClient())

    assert result["status"] == "completed"
    assert [call["name"] for call in result["tool_calls"]] == [expected_tool]
    assert result["tool_calls"][0]["result"]["ok"] is True
    assert "依据工具结果" in result["answer"]


class AmbiguousClient:
    """Try to continue after an ambiguous search to test the Python hard stop."""

    def __init__(self) -> None:
        self.count = 0

    async def create_chat_completion(self, messages, tools):
        self.count += 1
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "must_not_run",
                    "type": "function",
                    "function": {
                        "name": "get_student_scores",
                        "arguments": json.dumps({"student_id": "S001"}),
                    },
                },
                {
                    "id": "search_ambiguous",
                    "type": "function",
                    "function": {
                        "name": "search_student",
                        "arguments": json.dumps({"name_or_id": "张三"}, ensure_ascii=False),
                    },
                },
            ],
        }


async def test_duplicate_name_result_requires_clarification_data() -> None:
    client = AmbiguousClient()
    result = await run_agent("张三的成绩怎么样？", client=client)

    data = result["tool_calls"][0]["result"]["data"]
    assert result["status"] == "clarification_required"
    assert result["answer"].startswith("找到多名姓名为张三的学生，请确认：")
    assert "1. S001" in result["answer"]
    assert "2. S004" in result["answer"]
    assert data["status"] == "ambiguous"
    assert {candidate["student_id"] for candidate in data["candidates"]} == {"S001", "S004"}
    assert all(call["name"] != "get_student_scores" for call in result["tool_calls"])
    assert client.count == 1


async def test_comprehensive_analysis_stops_on_duplicate_name() -> None:
    result = await run_agent("综合分析张三", client=AmbiguousClient())

    assert result["status"] == "clarification_required"
    assert [call["name"] for call in result["tool_calls"]] == ["search_student"]


async def test_zhao_liu_is_reported_from_real_data_as_not_found() -> None:
    result = await run_agent("查询一个不存在的赵六", client=ScenarioClient())

    data = result["tool_calls"][0]["result"]["data"]
    assert data == {"status": "not_found"}


class InvalidArgumentsClient:
    def __init__(self) -> None:
        self.count = 0

    async def create_chat_completion(self, messages, tools):
        self.count += 1
        if self.count == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "bad_args",
                        "type": "function",
                        "function": {"name": "get_student_info", "arguments": "not-json"},
                    }
                ],
            }
        tool_result = json.loads(messages[-1]["content"])
        assert tool_result["error_code"] == "INVALID_TOOL_ARGUMENTS"
        return {"role": "assistant", "content": "工具参数无效，未执行查询。"}


async def test_invalid_model_arguments_are_not_executed() -> None:
    result = await run_agent("测试非法参数", client=InvalidArgumentsClient())

    assert result["status"] == "completed"
    assert result["tool_calls"][0]["result"]["error_code"] == "INVALID_TOOL_ARGUMENTS"


class EndlessToolClient:
    def __init__(self) -> None:
        self.count = 0

    async def create_chat_completion(self, messages, tools):
        self.count += 1
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_{self.count}",
                    "type": "function",
                    "function": {"name": "list_files", "arguments": "{}"},
                }
            ],
        }


async def test_tool_loop_stops_at_maximum_rounds() -> None:
    client = EndlessToolClient()
    result = await run_agent("持续调用", client=client, max_tool_rounds=2)

    assert result["status"] == "max_tool_rounds_exceeded"
    assert len(result["tool_calls"]) == 2
    assert client.count == 3


def test_only_read_only_tools_are_exposed() -> None:
    names = {tool["function"]["name"] for tool in TOOL_DEFINITIONS}

    assert names == EXPECTED_TOOL_NAMES


async def test_top_three_request_must_use_python_ranking_tool() -> None:
    client = MultiStepClient(
        [[("get_top_three_students", {})]],
        "平均成绩最高的三名学生已根据工具结果列出。",
    )

    result = await run_agent("成绩最高的三名学生是谁？", client=client)

    assert result["status"] == "completed"
    assert [call["name"] for call in result["tool_calls"]] == [
        "get_top_three_students"
    ]
    students = result["tool_calls"][0]["result"]["data"]["students"]
    assert [(item["student_id"], item["average_score"]) for item in students] == [
        ("S004", 93.33),
        ("S007", 91.0),
        ("S001", 90.67),
    ]


class MultiStepClient:
    """Return a deterministic sequence of dependent Tool Calling rounds."""

    def __init__(self, steps: list[list[tuple[str, dict[str, Any]]]], final_answer: str):
        self.steps = steps
        self.final_answer = final_answer
        self.count = 0

    async def create_chat_completion(self, messages, tools):
        if self.count < len(self.steps):
            round_index = self.count + 1
            calls = []
            for call_index, (name, arguments) in enumerate(self.steps[self.count], start=1):
                calls.append(
                    {
                        "id": f"round_{round_index}_call_{call_index}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                )
            self.count += 1
            return {"role": "assistant", "content": None, "tool_calls": calls}

        self.count += 1
        assert messages[-1]["role"] == "tool"
        return {"role": "assistant", "content": self.final_answer}


async def test_comprehensive_analysis_calls_all_three_student_tools(caplog) -> None:
    client = MultiStepClient(
        [
            [("get_student_info", {"student_id": "S001"})],
            [("get_student_scores", {"student_id": "S001"})],
            [("get_student_research", {"student_id": "S001"})],
        ],
        "S001 综合分析完成。",
    )
    caplog.set_level("INFO", logger="backend.agent")

    result = await run_agent("综合分析S001", client=client)

    assert result["status"] == "completed"
    assert [call["name"] for call in result["tool_calls"]] == [
        "get_student_info",
        "get_student_scores",
        "get_student_research",
    ]
    assert all(call["result"]["ok"] is True for call in result["tool_calls"])
    assert "round=1 tool_name=get_student_info" in caplog.text
    assert "round=2 tool_name=get_student_scores" in caplog.text
    assert "round=3 tool_name=get_student_research" in caplog.text
    assert "tool_result=" in caplog.text


async def test_comprehensive_comparison_uses_required_sequence() -> None:
    client = MultiStepClient(
        [
            [
                ("get_student_info", {"student_id": "S001"}),
                ("get_student_info", {"student_id": "S002"}),
            ],
            [
                ("get_student_scores", {"student_id": "S001"}),
                ("get_student_scores", {"student_id": "S002"}),
                ("get_student_research", {"student_id": "S001"}),
                ("get_student_research", {"student_id": "S002"}),
            ],
            [("compare_students", {"student_ids": ["S001", "S002"]})],
        ],
        "S001 与 S002 综合比较完成。",
    )

    result = await run_agent("比较S001和S002的综合情况", client=client)

    assert result["status"] == "completed"
    assert [call["name"] for call in result["tool_calls"]] == [
        "get_student_info",
        "get_student_info",
        "get_student_scores",
        "get_student_scores",
        "get_student_research",
        "get_student_research",
        "compare_students",
    ]
    comparison = result["tool_calls"][-1]["result"]
    assert comparison["ok"] is True
    assert comparison["data"]["differences"][0]["values"]["average_score"] == 5.0


async def test_compare_is_not_executed_before_required_source_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_early_compare(student_ids):
        raise AssertionError("compare_students must not execute before prerequisite calls")

    monkeypatch.setitem(
        agent_module.TOOL_FUNCTIONS,
        "compare_students",
        forbidden_early_compare,
    )
    class EarlyCompareClient:
        def __init__(self):
            self.count = 0

        async def create_chat_completion(self, messages, tools):
            self.count += 1
            if self.count == 1:
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "early_compare",
                            "type": "function",
                            "function": {
                                "name": "compare_students",
                                "arguments": json.dumps(
                                    {"student_ids": ["S001", "S002"]}
                                ),
                            },
                        }
                    ],
                }
            return {"role": "assistant", "content": "需要先查询双方数据。"}

    client = EarlyCompareClient()

    result = await run_agent("比较S001和S002的综合情况", client=client)

    assert result["status"] == "incomplete_tool_calls"
    assert result["tool_calls"][0]["result"]["error_code"] == "TOOL_SEQUENCE_ERROR"


async def test_missing_research_answer_keeps_no_record_meaning() -> None:
    client = MultiStepClient(
        [
            [("get_student_info", {"student_id": "S011"})],
            [("get_student_scores", {"student_id": "S011"})],
            [("get_student_research", {"student_id": "S011"})],
        ],
        "该学生没有科研成果。",
    )

    result = await run_agent("综合分析S011", client=client)

    research_call = result["tool_calls"][-1]
    assert research_call["result"]["data"]["status"] == "no_record"
    assert research_call["result"]["message"] == "当前科研成果材料中未查询到相关记录。"
    assert "当前科研成果材料中未查询到相关记录。" in result["answer"]
    assert "没有科研成果" not in result["answer"]


class PrematureAnswerClient:
    """Attempt to answer after one Tool so the Agent must request missing Tools."""

    def __init__(self) -> None:
        self.count = 0

    async def create_chat_completion(self, messages, tools):
        self.count += 1
        if self.count == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "info_only",
                        "type": "function",
                        "function": {
                            "name": "get_student_info",
                            "arguments": json.dumps({"student_id": "S001"}),
                        },
                    }
                ],
            }
        if self.count == 2:
            return {"role": "assistant", "content": "仅根据基本信息提前回答。"}
        if self.count == 3:
            assert "系统校验发现任务尚未完成" in messages[-1]["content"]
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "scores",
                        "type": "function",
                        "function": {
                            "name": "get_student_scores",
                            "arguments": json.dumps({"student_id": "S001"}),
                        },
                    },
                    {
                        "id": "research",
                        "type": "function",
                        "function": {
                            "name": "get_student_research",
                            "arguments": json.dumps({"student_id": "S001"}),
                        },
                    },
                ],
            }
        assert messages[-1]["role"] == "tool"
        return {"role": "assistant", "content": "完整综合分析。"}


async def test_comprehensive_analysis_rejects_premature_final_answer() -> None:
    result = await run_agent("综合分析S001", client=PrematureAnswerClient())

    assert result["status"] == "completed"
    assert result["answer"] == "完整综合分析。"
    assert [call["name"] for call in result["tool_calls"]] == [
        "get_student_info",
        "get_student_scores",
        "get_student_research",
    ]
