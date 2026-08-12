"""Tool-calling loop for the read-only Student Document Agent."""

import json
from typing import Any, Protocol

from backend.llm_client import LLMClient
from backend.tools.analysis_tools import compare_students
from backend.tools.file_tools import list_files
from backend.tools.student_tools import (
    get_student_info,
    get_student_research,
    get_student_scores,
    search_student,
)


MAX_TOOL_ROUNDS = 8

SYSTEM_PROMPT = """你是学生材料智能文档助手。
必须遵守以下规则：
1. 所有学生结构化信息只能通过工具获取，不允许根据常识、姓名或上下文猜测。
2. 不得伪造任何学生数据；最终回答只能依据当前对话中的工具结果。
3. 涉及两名或多名学生的数值比较时，必须使用 compare_students，不能自行计算差值。
4. “文件或表中没有该学生的记录”不等于“已确认该学生的数值为零”，必须保留 no_record 语义。
5. 姓名搜索出现 ambiguous 时，必须列出候选人并请用户用学号澄清，禁止自行选择。
6. 文件不存在时必须明确说明文件不存在。
7. 查询学生详情、成绩或科研前，如用户只提供姓名，应先用 search_student 确认唯一学号。
8. 不提供写文件能力，也不得声称已经修改任何文件。
请用简洁中文整合工具结果并回答。"""


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出 data 目录中所有受支持的 Excel 和 Word 文件。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_student",
            "description": "按完整姓名或学号搜索学生；用于识别重名、唯一匹配或不存在。",
            "parameters": {
                "type": "object",
                "properties": {"name_or_id": {"type": "string", "description": "学生姓名或学号"}},
                "required": ["name_or_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_student_info",
            "description": "通过学号查询学生基本信息。只能传入已确认的学号。",
            "parameters": {
                "type": "object",
                "properties": {"student_id": {"type": "string", "description": "学生学号"}},
                "required": ["student_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_student_scores",
            "description": "通过学号查询三科成绩、Python 计算的平均分和专业排名。",
            "parameters": {
                "type": "object",
                "properties": {"student_id": {"type": "string", "description": "学生学号"}},
                "required": ["student_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_student_research",
            "description": "通过学号查询科研记录，并区分 no_record 与数值为零。",
            "parameters": {
                "type": "object",
                "properties": {"student_id": {"type": "string", "description": "学生学号"}},
                "required": ["student_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_students",
            "description": "比较至少两名学生的平均成绩、专业排名和科研数量，由 Python 计算差值。",
            "parameters": {
                "type": "object",
                "properties": {
                    "student_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "description": "至少两个学生学号",
                    }
                },
                "required": ["student_ids"],
                "additionalProperties": False,
            },
        },
    },
]


TOOL_FUNCTIONS = {
    "list_files": list_files,
    "search_student": search_student,
    "get_student_info": get_student_info,
    "get_student_scores": get_student_scores,
    "get_student_research": get_student_research,
    "compare_students": compare_students,
}

TOOL_ARGUMENTS = {
    "list_files": {},
    "search_student": {"name_or_id": str},
    "get_student_info": {"student_id": str},
    "get_student_scores": {"student_id": str},
    "get_student_research": {"student_id": str},
    "compare_students": {"student_ids": list},
}


class ChatClient(Protocol):
    async def create_chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]: ...


def _invalid_tool_result(error_code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error_code": error_code, "message": message}


def _execute_tool(name: str, raw_arguments: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate model-generated arguments and execute an allow-listed tool."""
    if name not in TOOL_FUNCTIONS:
        return {}, _invalid_tool_result("UNKNOWN_TOOL", f"不允许调用工具：{name}")
    try:
        arguments = json.loads(raw_arguments or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}, _invalid_tool_result("INVALID_TOOL_ARGUMENTS", "工具参数不是有效 JSON")
    if not isinstance(arguments, dict):
        return {}, _invalid_tool_result("INVALID_TOOL_ARGUMENTS", "工具参数必须是 JSON 对象")

    expected = TOOL_ARGUMENTS[name]
    if set(arguments) != set(expected):
        return arguments, _invalid_tool_result(
            "INVALID_TOOL_ARGUMENTS", f"工具 {name} 的参数字段不正确"
        )
    for field, field_type in expected.items():
        if not isinstance(arguments[field], field_type):
            return arguments, _invalid_tool_result(
                "INVALID_TOOL_ARGUMENTS", f"工具参数 {field} 类型不正确"
            )
        if field_type is str and not arguments[field].strip():
            return arguments, _invalid_tool_result(
                "INVALID_TOOL_ARGUMENTS", f"工具参数 {field} 不能为空"
            )
    if name == "compare_students":
        student_ids = arguments["student_ids"]
        if len(student_ids) < 2 or any(not isinstance(item, str) or not item.strip() for item in student_ids):
            return arguments, _invalid_tool_result(
                "INVALID_TOOL_ARGUMENTS", "compare_students 至少需要两个有效学号"
            )

    try:
        result = TOOL_FUNCTIONS[name](**arguments)
    except Exception:
        result = _invalid_tool_result("TOOL_EXECUTION_ERROR", f"工具 {name} 执行失败")
    return arguments, result


async def run_agent(
    message: str,
    client: ChatClient | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
) -> dict[str, Any]:
    """Run the model/tool loop until a final answer or the safety limit is reached."""
    llm_client = client or LLMClient()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]
    executed_calls: list[dict[str, Any]] = []
    tool_rounds = 0

    for _ in range(max_tool_rounds + 1):
        assistant_message = await llm_client.create_chat_completion(messages, TOOL_DEFINITIONS)
        tool_calls = assistant_message.get("tool_calls") or []
        if not tool_calls:
            answer = assistant_message.get("content")
            if not isinstance(answer, str) or not answer.strip():
                return {
                    "answer": "模型未返回有效回答。",
                    "tool_calls": executed_calls,
                    "status": "error",
                }
            return {"answer": answer.strip(), "tool_calls": executed_calls, "status": "completed"}

        if tool_rounds >= max_tool_rounds:
            return {
                "answer": "工具调用次数达到上限，请简化问题后重试。",
                "tool_calls": executed_calls,
                "status": "max_tool_rounds_exceeded",
            }
        tool_rounds += 1

        normalized_calls = []
        for index, tool_call in enumerate(tool_calls):
            function = tool_call.get("function") or {}
            normalized_calls.append(
                {
                    "id": str(tool_call.get("id") or f"missing_tool_call_id_{index}"),
                    "type": "function",
                    "function": {
                        "name": str(function.get("name") or ""),
                        "arguments": function.get("arguments") or "{}",
                    },
                }
            )
        messages.append({"role": "assistant", "content": assistant_message.get("content"), "tool_calls": normalized_calls})

        for tool_call in normalized_calls:
            name = tool_call["function"]["name"]
            arguments, result = _execute_tool(name, tool_call["function"]["arguments"])
            executed_calls.append({"name": name, "arguments": arguments, "result": result})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    return {
        "answer": "工具调用次数达到上限，请简化问题后重试。",
        "tool_calls": executed_calls,
        "status": "max_tool_rounds_exceeded",
    }
