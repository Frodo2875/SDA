"""Tool-calling loop for the read-only Student Document Agent."""

import json
import logging
import re
from typing import Any, Protocol

from backend import database
from backend.llm_client import LLMClient
from backend.services.confirmation import create_pending_action
from backend.tools.analysis_tools import compare_students
from backend.tools.file_tools import list_files
from backend.tools.student_tools import (
    get_student_info,
    get_student_research,
    get_student_scores,
    search_student,
)


MAX_TOOL_ROUNDS = 8
LOGGER = logging.getLogger(__name__)
MISSING_RESEARCH_MESSAGE = "当前科研成果材料中未查询到相关记录。"

SYSTEM_PROMPT = """你是学生材料智能文档助手。
必须遵守以下规则：
1. 所有学生结构化信息只能通过工具获取，不允许根据常识、姓名或上下文猜测。
2. 不得伪造任何学生数据；最终回答只能依据当前对话中的工具结果。
3. 涉及两名或多名学生的数值比较时，必须使用 compare_students，不能自行计算差值。
4. “文件或表中没有该学生的记录”不等于“已确认该学生的数值为零”，必须保留 no_record 语义。
5. 姓名搜索出现 ambiguous 时，必须列出候选人并请用户用学号澄清，禁止自行选择。
6. 文件不存在时必须明确说明文件不存在。
7. 查询学生详情、成绩或科研前，如用户只提供姓名，应先用 search_student 确认唯一学号。
8. write_word 不对你开放。用户要求写入时，你只能先生成待确认的综合评价内容，不得声称文件已经修改或用户已经确认。
9. “综合分析某位学生”必须完整调用 get_student_info、get_student_scores、get_student_research，不能只依据其中一个工具回答。
10. 比较两名或多名学生的综合情况时，必须依次完成：用 get_student_info 确认每名学生身份；用 get_student_scores 和 get_student_research 查询每名学生数据；最后调用 compare_students。不能跳过前置查询。
11. get_student_research 返回 no_record 时，必须原样说明“当前科研成果材料中未查询到相关记录。”，不得说该学生没有科研成果或科研成果为零。
12. 用户要求生成综合评价并写入 Word 时，必须先完整查询基本信息、成绩和科研，再生成适合直接追加到 Word 的评价正文。不要在正文中加入“已写入”“已确认”等表述。
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


def _execute_tool(
    name: str,
    raw_arguments: Any,
    executed_calls: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
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
        if executed_calls is not None:
            sequence_error = _validate_compare_sequence(arguments, executed_calls)
            if sequence_error is not None:
                return arguments, sequence_error

    try:
        result = TOOL_FUNCTIONS[name](**arguments)
    except Exception:
        result = _invalid_tool_result("TOOL_EXECUTION_ERROR", f"工具 {name} 执行失败")
    return arguments, result


def _has_successful_call(
    executed_calls: list[dict[str, Any]], tool_name: str, student_id: str
) -> bool:
    """Return whether a successful student-specific tool call already occurred."""
    return any(
        call["name"] == tool_name
        and call["arguments"].get("student_id") == student_id
        and call["result"].get("ok") is True
        for call in executed_calls
    )


def _validate_compare_sequence(
    arguments: dict[str, Any], executed_calls: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Require identity and source-data calls before an Agent comparison."""
    student_ids = arguments.get("student_ids")
    if not isinstance(student_ids, list):
        return None

    missing_steps = []
    for student_id in student_ids:
        for tool_name in (
            "get_student_info",
            "get_student_scores",
            "get_student_research",
        ):
            if not _has_successful_call(executed_calls, tool_name, student_id):
                missing_steps.append({"tool_name": tool_name, "student_id": student_id})

    if not missing_steps:
        return None
    return _invalid_tool_result(
        "TOOL_SEQUENCE_ERROR",
        "比较前必须先确认每名学生身份，并查询双方成绩和科研数据。"
        f"缺少步骤：{json.dumps(missing_steps, ensure_ascii=False)}",
    )


def _log_tool_call(
    round_number: int,
    tool_name: str,
    arguments: dict[str, Any],
    tool_result: dict[str, Any],
) -> None:
    """Write one structured backend log without exposing model reasoning."""
    LOGGER.info(
        "agent_tool_call round=%s tool_name=%s arguments=%s tool_result=%s",
        round_number,
        tool_name,
        json.dumps(arguments, ensure_ascii=False),
        json.dumps(tool_result, ensure_ascii=False),
    )


def _ambiguity_response(result: dict[str, Any]) -> dict[str, str]:
    """Build a deterministic clarification response from Tool candidates."""
    candidates = result["data"].get("candidates", [])
    name = candidates[0].get("name", "该姓名") if candidates else "该姓名"
    lines = [f"找到多名姓名为{name}的学生，请确认：", ""]
    for index, candidate in enumerate(candidates, start=1):
        lines.append(
            f"{index}. {candidate.get('student_id')} {candidate.get('name')}，"
            f"{candidate.get('major')}，{candidate.get('grade')}，"
            f"{candidate.get('class_name')}"
        )
    return {"answer": "\n".join(lines), "status": "clarification_required"}


def _normalize_final_answer(
    answer: str, executed_calls: list[dict[str, Any]]
) -> str:
    """Preserve the exact no-record meaning in the user-facing answer."""
    has_missing_research = any(
        call["name"] == "get_student_research"
        and call["result"].get("ok") is True
        and isinstance(call["result"].get("data"), dict)
        and call["result"]["data"].get("status") == "no_record"
        for call in executed_calls
    )
    if not has_missing_research:
        return answer

    normalized = answer
    for misleading_phrase in ("该学生没有科研成果", "没有科研成果", "科研成果为零"):
        normalized = normalized.replace(misleading_phrase, MISSING_RESEARCH_MESSAGE)
    normalized = normalized.replace("。。", "。")
    if MISSING_RESEARCH_MESSAGE not in normalized:
        normalized = f"{MISSING_RESEARCH_MESSAGE}\n\n{normalized}"
    return normalized


def _target_student_ids(
    user_message: str, executed_calls: list[dict[str, Any]]
) -> list[str]:
    """Resolve explicit IDs plus IDs confirmed by successful identity searches."""
    student_ids = [item.upper() for item in re.findall(r"S\d+", user_message, re.I)]
    for call in executed_calls:
        if call["name"] != "search_student" or call["result"].get("ok") is not True:
            continue
        data = call["result"].get("data")
        if isinstance(data, dict) and data.get("status") == "found":
            student_id = data.get("student", {}).get("student_id")
            if isinstance(student_id, str):
                student_ids.append(student_id)
    return list(dict.fromkeys(student_ids))


def _write_request_target(user_message: str) -> str | None:
    """Extract a Word target only from an explicit write instruction."""
    match = re.search(
        r"写入\s*[“\"']?([^“”\"'\s，。；;！？!?]+\.docx)",
        user_message,
        re.I,
    )
    return match.group(1) if match else None


def _is_write_request(user_message: str) -> bool:
    return "写入" in user_message and ".docx" in user_message.lower()


def _student_identity_for_action(
    student_id: str, executed_calls: list[dict[str, Any]]
) -> dict[str, str] | None:
    """Read the confirmed student identity from an executed Tool result."""
    for call in executed_calls:
        if (
            call["name"] == "get_student_info"
            and call["arguments"].get("student_id") == student_id
            and call["result"].get("ok") is True
        ):
            data = call["result"].get("data")
            if isinstance(data, dict):
                name = data.get("name")
                if isinstance(name, str) and name.strip():
                    return {"student_id": student_id, "student_name": name.strip()}
    return None


def _missing_required_calls(
    user_message: str, executed_calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return required calls missing before a complex task may finish."""
    student_ids = _target_student_ids(user_message, executed_calls)
    required: list[tuple[str, str | None]] = []

    if "综合分析" in user_message and student_ids:
        for student_id in student_ids:
            required.extend(
                (tool_name, student_id)
                for tool_name in (
                    "get_student_info",
                    "get_student_scores",
                    "get_student_research",
                )
            )
    if _is_write_request(user_message) and student_ids:
        for student_id in student_ids:
            required.extend(
                (tool_name, student_id)
                for tool_name in (
                    "get_student_info",
                    "get_student_scores",
                    "get_student_research",
                )
            )
    if "比较" in user_message and "综合" in user_message and len(student_ids) >= 2:
        for student_id in student_ids:
            required.extend(
                (tool_name, student_id)
                for tool_name in (
                    "get_student_info",
                    "get_student_scores",
                    "get_student_research",
                )
            )
        if not any(
            call["name"] == "compare_students" and call["result"].get("ok") is True
            for call in executed_calls
        ):
            required.append(("compare_students", None))

    missing = []
    for tool_name, student_id in required:
        if student_id is None:
            missing.append({"tool_name": tool_name})
        elif not _has_successful_call(executed_calls, tool_name, student_id):
            missing.append({"tool_name": tool_name, "student_id": student_id})
    return missing


async def _run_agent_core(
    message: str,
    session_id: str,
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
    incomplete_answer_retries = 0

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
            missing_calls = _missing_required_calls(message, executed_calls)
            if missing_calls:
                if incomplete_answer_retries >= 2:
                    return {
                        "answer": "模型未完成任务所需的全部工具查询，请重试。",
                        "tool_calls": executed_calls,
                        "status": "incomplete_tool_calls",
                    }
                incomplete_answer_retries += 1
                messages.append(
                    {"role": "assistant", "content": answer.strip()}
                )
                messages.append(
                    {
                        "role": "user",
                        "content": "系统校验发现任务尚未完成，请继续调用缺少的工具："
                        + json.dumps(missing_calls, ensure_ascii=False)
                        + "。完成后再生成最终回答。",
                    }
                )
                continue
            normalized_answer = _normalize_final_answer(answer.strip(), executed_calls)
            if _is_write_request(message):
                target_file = _write_request_target(message)
                student_ids = _target_student_ids(message, executed_calls)
                if target_file is None:
                    return {
                        "answer": "无法识别要写入的 Word 文件名，请明确提供 .docx 文件名。",
                        "tool_calls": executed_calls,
                        "status": "error",
                    }
                if len(student_ids) != 1:
                    return {
                        "answer": "写入综合评价时必须确认唯一学生学号。",
                        "tool_calls": executed_calls,
                        "status": "error",
                    }
                identity = _student_identity_for_action(student_ids[0], executed_calls)
                if identity is None:
                    return {
                        "answer": "尚未通过工具确认学生身份，不能创建写入操作。",
                        "tool_calls": executed_calls,
                        "status": "error",
                    }
                action_result = create_pending_action(
                    session_id=session_id,
                    target_file=target_file,
                    student_id=identity["student_id"],
                    student_name=identity["student_name"],
                    content=normalized_answer,
                )
                if not action_result["ok"]:
                    return {
                        "answer": action_result["message"],
                        "tool_calls": executed_calls,
                        "status": "error",
                    }
                return {
                    "answer": "综合评价已生成，等待用户确认。当前尚未写入文件。",
                    "tool_calls": executed_calls,
                    "status": "confirmation_required",
                    "pending_action": action_result["data"],
                }
            return {
                "answer": normalized_answer,
                "tool_calls": executed_calls,
                "status": "completed",
            }

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

        tool_priority = {
            "search_student": 0,
            "get_student_info": 1,
            "get_student_scores": 2,
            "get_student_research": 2,
            "compare_students": 3,
        }
        ordered_calls = sorted(
            normalized_calls,
            key=lambda call: tool_priority.get(call["function"]["name"], 4),
        )
        for tool_call in ordered_calls:
            name = tool_call["function"]["name"]
            arguments, result = _execute_tool(
                name, tool_call["function"]["arguments"], executed_calls
            )
            executed_calls.append({"name": name, "arguments": arguments, "result": result})
            _log_tool_call(tool_rounds, name, arguments, result)

            if (
                name == "search_student"
                and result.get("ok") is True
                and isinstance(result.get("data"), dict)
                and result["data"].get("status") == "ambiguous"
            ):
                clarification = _ambiguity_response(result)
                return {
                    "answer": clarification["answer"],
                    "tool_calls": executed_calls,
                    "status": clarification["status"],
                }

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


async def run_agent(
    message: str,
    client: ChatClient | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
    session_id: str = "direct",
) -> dict[str, Any]:
    """Run one Agent request and persist both sides of the chat."""
    database.save_chat_message(
        session_id=session_id,
        role="user",
        content=message,
        used_tools=[],
        status="received",
    )
    try:
        result = await _run_agent_core(
            message=message,
            session_id=session_id,
            client=client,
            max_tool_rounds=max_tool_rounds,
        )
    except Exception:
        database.save_chat_message(
            session_id=session_id,
            role="assistant",
            content="请求处理失败",
            used_tools=[],
            status="error",
        )
        raise

    database.save_chat_message(
        session_id=session_id,
        role="assistant",
        content=result["answer"],
        used_tools=result.get("tool_calls", []),
        status=result["status"],
    )
    return result
