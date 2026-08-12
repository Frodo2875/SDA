"""Tests for the LLM tool-calling loop using a scripted model and real tools."""

import json
from typing import Any

import pytest

from backend.agent import TOOL_DEFINITIONS, run_agent


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


SCENARIOS = {
    "查询S001的学生信息": ("get_student_info", {"student_id": "S001"}),
    "张三的成绩怎么样？": ("search_student", {"name_or_id": "张三"}),
    "查询张三": ("search_student", {"name_or_id": "张三"}),
    "现在知识库有哪些文件？": ("list_files", {}),
    "比较S001和S002的成绩": (
        "compare_students",
        {"student_ids": ["S001", "S002"]},
    ),
    "查询一个不存在的赵六": ("search_student", {"name_or_id": "赵六"}),
}


class ScenarioClient:
    """Script model tool choices while deriving final answers from tool messages."""

    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []

    async def create_chat_completion(self, messages, tools):
        self.requests.append(messages.copy())
        assert {item["function"]["name"] for item in tools} == {
            "list_files",
            "search_student",
            "get_student_info",
            "get_student_scores",
            "get_student_research",
            "compare_students",
        }
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


async def test_duplicate_name_result_requires_clarification_data() -> None:
    result = await run_agent("张三的成绩怎么样？", client=ScenarioClient())

    data = result["tool_calls"][0]["result"]["data"]
    assert data["status"] == "ambiguous"
    assert {candidate["student_id"] for candidate in data["candidates"]} == {"S001", "S004"}
    assert all(call["name"] != "get_student_scores" for call in result["tool_calls"])


async def test_zhao_liu_is_reported_from_real_data_as_found() -> None:
    result = await run_agent("查询一个不存在的赵六", client=ScenarioClient())

    data = result["tool_calls"][0]["result"]["data"]
    assert data["status"] == "found"
    assert data["student"]["student_id"] == "S005"


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

    assert names == {
        "list_files",
        "search_student",
        "get_student_info",
        "get_student_scores",
        "get_student_research",
        "compare_students",
    }
