"""Tool-calling loop for the read-only Student Document Agent."""

import json
import logging
import re
import time
from typing import Any, Protocol

from pydantic import ValidationError

from backend import database
from backend.llm_client import LLMClient
from backend.runtime.planner import create_plan
from backend.runtime.task_runner import (
    complete_generation_step,
    finalize_task,
    record_tool_execution,
    start_generation_step,
    start_task,
    start_tool_step,
)
from backend.runtime.tool_executor import execute_with_retry
from backend.runtime.safety_policy import (
    PolicyDecision,
    assess_tool_execution,
    record_safety_trace,
)
from backend.runtime.context_manager import resolve_message, update_after_run
from backend.services.confirmation import create_pending_action
from backend.services.redaction import redacted_json, redact_value
from backend.services.trace_service import llm_usage_metrics, record_trace
from backend.tool_registry import TOOL_REGISTRY


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
13. 用户询问全体学生中平均成绩最高的三名时，必须调用 get_top_three_students，禁止自行枚举或猜测排名。
14. 查询未知结构 Excel 前必须使用已保存 Schema；精确筛选使用 query_table，统计使用 aggregate_table。
15. 不得生成 SQL、Python 或任意表达式作为工具参数，只能使用工具定义的结构化字段和白名单操作符。
16. 回答 PDF 或 Word 中的制度、规则和长文本内容时必须使用 retrieve_document，并且只能依据返回的 Evidence；若状态为 not_found，必须回答“当前材料中未找到足够依据。”，不得用模型自身知识补写。
17. 判断奖学金资格时必须依次调用 search_student、query_table 查询成绩、query_table 查询科研、retrieve_document 查询评审规则，最后调用 evaluate_scholarship_eligibility。不得自行比较阈值；不得把阈值或学生数值作为判断 Tool 参数。
18. evaluate_scholarship_eligibility 返回 insufficient_evidence 时必须回答“当前材料不足以判断。”并列出缺失项；“本次结论使用了”只能列出该 Tool 返回的 evidence_chain 和 used_tools。
请用简洁中文整合工具结果并回答。"""


TOOL_DEFINITIONS: list[dict[str, Any]] = TOOL_REGISTRY.definitions()
TOOL_FUNCTIONS = TOOL_REGISTRY.handlers()
TOOL_ARGUMENTS = TOOL_REGISTRY.legacy_argument_types()


class ChatClient(Protocol):
    async def create_chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]: ...


def _invalid_tool_result(error_code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error_code": error_code, "message": message}


def _argument_object(raw_arguments: Any) -> dict[str, Any]:
    """Return a non-executable preview of model arguments for Step claiming."""
    try:
        parsed = json.loads(raw_arguments or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _execute_tool(
    name: str,
    raw_arguments: Any,
    executed_calls: list[dict[str, Any]] | None = None,
    policy_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate model-generated arguments and execute an allow-listed tool."""
    spec = TOOL_REGISTRY.get(name)
    if name not in TOOL_FUNCTIONS or spec is None:
        return {}, _invalid_tool_result("UNKNOWN_TOOL", f"不允许调用工具：{name}")
    try:
        arguments = json.loads(raw_arguments or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}, _invalid_tool_result("INVALID_TOOL_ARGUMENTS", "工具参数不是有效 JSON")
    if not isinstance(arguments, dict):
        return {}, _invalid_tool_result("INVALID_TOOL_ARGUMENTS", "工具参数必须是 JSON 对象")

    try:
        validated = spec.arguments_model.model_validate(arguments)
    except ValidationError:
        return arguments, _invalid_tool_result(
            "INVALID_TOOL_ARGUMENTS", f"工具 {name} 的参数未通过 Schema 校验"
        )
    arguments = validated.model_dump()
    assessment = assess_tool_execution(
        tool_name=name,
        arguments=arguments,
        read_only=spec.read_only,
        requires_confirmation=spec.requires_confirmation,
    )
    context = policy_context or {}
    try:
        record_safety_trace(
            assessment,
            session_id=str(context.get("session_id") or "agent-policy"),
            task_id=context.get("task_id"),
            step_id=context.get("step_id"),
            tool_name=name,
        )
    except Exception:
        pass
    if assessment.decision == PolicyDecision.BLOCK:
        return arguments, _invalid_tool_result(
            "SAFETY_POLICY_BLOCKED", assessment.reason
        )
    if assessment.decision == PolicyDecision.CONFIRMATION_REQUIRED:
        return arguments, _invalid_tool_result(
            "CONFIRMATION_REQUIRED", assessment.reason
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

    evidence_context = None
    if name == "evaluate_scholarship_eligibility":
        if executed_calls is None:
            return arguments, _invalid_tool_result(
                "TOOL_SEQUENCE_ERROR", "奖学金判断缺少本轮工具证据上下文"
            )
        sequence_error = _validate_scholarship_sequence(arguments, executed_calls)
        if sequence_error is not None:
            return arguments, sequence_error
        evidence_context = _scholarship_context(arguments, executed_calls)

    try:
        if evidence_context is None:
            result = TOOL_FUNCTIONS[name](**arguments)
        else:
            result = TOOL_FUNCTIONS[name](
                **arguments, evidence_context=evidence_context
            )
    except OSError:
        result = _invalid_tool_result(
            "TEMPORARY_IO_ERROR", f"工具 {name} 遇到临时 IO 错误"
        )
    except Exception:
        result = _invalid_tool_result("TOOL_EXECUTION_ERROR", f"工具 {name} 执行失败")
    return arguments, result


def _execute_tool_with_retry(
    name: str,
    raw_arguments: Any,
    executed_calls: list[dict[str, Any]] | None = None,
    policy_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Add bounded retries around the existing validated Tool execution path."""
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        arguments, result = _execute_tool(
            name, raw_arguments, executed_calls, policy_context
        )
        return arguments, result, 0
    return execute_with_retry(
        tool_name=name,
        raw_arguments=raw_arguments,
        execute_once=lambda current_arguments: _execute_tool(
            name, current_arguments, executed_calls, policy_context
        ),
        retryable=spec.retryable,
        read_only=spec.read_only,
        requires_confirmation=spec.requires_confirmation,
    )


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


def _query_call_categories(call: dict[str, Any], student_id: str) -> set[str]:
    if call["name"] != "query_table" or call["result"].get("ok") is not True:
        return set()
    evidence = call["result"].get("evidence_chain") or []
    fields = {
        str(item.get("field") or "").replace("_", "").casefold()
        for item in evidence
        if item.get("record_key") == student_id
    }
    fields.update(
        str(field).replace("_", "").casefold()
        for field in (call.get("arguments", {}).get("select") or [])
    )
    categories = set()
    if fields.intersection({"平均分", "平均成绩", "averagescore", "专业排名", "排名", "rank"}):
        categories.add("score")
    if fields.intersection({"论文数", "论文数量", "专利数", "专利数量", "竞赛数", "竞赛数量", "papers", "patents", "competitions"}):
        categories.add("research")
    return categories


def _validate_scholarship_sequence(
    arguments: dict[str, Any], executed_calls: list[dict[str, Any]]
) -> dict[str, Any] | None:
    student_id = arguments["student_id"]
    identity_found = any(
        call["name"] == "search_student"
        and call["result"].get("ok") is True
        and (call["result"].get("data") or {}).get("status") == "found"
        and (call["result"].get("data") or {}).get("student", {}).get("student_id")
        == student_id
        for call in executed_calls
    )
    categories = {
        category
        for call in executed_calls
        for category in _query_call_categories(call, student_id)
    }
    has_rules = any(
        call["name"] == "retrieve_document"
        and call["result"].get("ok") is True
        for call in executed_calls
    )
    missing = []
    if not identity_found:
        missing.append("search_student")
    if "score" not in categories:
        missing.append("query_table:成绩")
    if "research" not in categories:
        missing.append("query_table:科研")
    if not has_rules:
        missing.append("retrieve_document:规则")
    if not missing:
        return None
    return _invalid_tool_result(
        "TOOL_SEQUENCE_ERROR",
        "奖学金判断前必须完成身份、成绩、科研和规则证据查询。缺少："
        + "、".join(missing),
    )


def _scholarship_context(
    arguments: dict[str, Any], executed_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    student_id = arguments["student_id"]
    identity_call = next(
        (
            call
            for call in executed_calls
            if call["name"] == "search_student"
            and (call["result"].get("data") or {}).get("status") == "found"
            and (call["result"].get("data") or {}).get("student", {}).get("student_id")
            == student_id
        ),
        None,
    )
    return {
        "identity_call": identity_call,
        "query_calls": [
            call
            for call in executed_calls
            if call["name"] == "query_table"
            and _query_call_categories(call, student_id)
        ],
        "retrieval_calls": [
            call
            for call in executed_calls
            if call["name"] == "retrieve_document"
            and call["result"].get("ok") is True
        ],
    }


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
        redacted_json(arguments),
        redacted_json(tool_result),
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
    scholarship_result = next(
        (
            call["result"].get("data")
            for call in reversed(executed_calls)
            if call["name"] == "evaluate_scholarship_eligibility"
            and call["result"].get("ok") is True
            and isinstance(call["result"].get("data"), dict)
        ),
        None,
    )
    if scholarship_result is not None:
        return str(scholarship_result["answer"])

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
        r"写(?:入|进)\s*[“\"']?([^“”\"'\s，。；;！？!?]+\.docx)",
        user_message,
        re.I,
    )
    return match.group(1) if match else None


def _is_write_request(user_message: str) -> bool:
    return any(word in user_message for word in ("写入", "写进")) and ".docx" in user_message.lower()


def _is_top_three_request(user_message: str) -> bool:
    return (
        "成绩" in user_message
        and "最高" in user_message
        and any(word in user_message for word in ("三名", "3名", "前三"))
    )


def _is_scholarship_evaluation_request(user_message: str) -> bool:
    return "奖学金" in user_message and any(
        word in user_message for word in ("判断", "满足", "符合", "条件", "资格")
    )


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
    if _is_top_three_request(user_message) and not any(
        call["name"] == "get_top_three_students" and call["result"].get("ok") is True
        for call in executed_calls
    ):
        required.append(("get_top_three_students", None))
    if _is_scholarship_evaluation_request(user_message):
        student_id = student_ids[0] if len(student_ids) == 1 else None
        identity_ok = any(
            call["name"] == "search_student"
            and call["result"].get("ok") is True
            and (call["result"].get("data") or {}).get("status") == "found"
            for call in executed_calls
        )
        if not identity_ok:
            required.append(("search_student", None))
        if student_id is not None:
            categories = {
                category
                for call in executed_calls
                for category in _query_call_categories(call, student_id)
            }
            if "score" not in categories:
                required.append(("query_table:成绩", None))
            if "research" not in categories:
                required.append(("query_table:科研", None))
        if not any(
            call["name"] == "retrieve_document"
            and call["result"].get("ok") is True
            for call in executed_calls
        ):
            required.append(("retrieve_document:规则", None))
        if not any(
            call["name"] == "evaluate_scholarship_eligibility"
            and call["result"].get("ok") is True
            for call in executed_calls
        ):
            required.append(("evaluate_scholarship_eligibility", None))

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
    task_id: str | None = None,
    context_prompt: str | None = None,
) -> dict[str, Any]:
    """Run the model/tool loop until a final answer or the safety limit is reached."""
    llm_client = client or LLMClient()
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context_prompt:
        messages.append({"role": "system", "content": context_prompt})
    messages.append({"role": "user", "content": message})
    executed_calls: list[dict[str, Any]] = []
    tool_rounds = 0
    incomplete_answer_retries = 0

    for _ in range(max_tool_rounds + 1):
        if task_id is not None:
            start_generation_step(task_id)
        llm_started = time.perf_counter()
        assistant_message = dict(
            await llm_client.create_chat_completion(messages, TOOL_DEFINITIONS)
        )
        llm_duration_ms = max(
            0, round((time.perf_counter() - llm_started) * 1000)
        )
        usage = llm_usage_metrics(assistant_message.pop("_usage", {}))
        model_name = str(assistant_message.pop("_model", "") or "unknown")
        try:
            record_trace(
                session_id=session_id,
                task_id=task_id,
                step_id=_current_step_id(task_id),
                event_type="llm_call",
                tool_name="llm",
                arguments={
                    "message_count": len(messages),
                    "available_tool_count": len(TOOL_DEFINITIONS),
                    "model": model_name,
                },
                result={"ok": True, "status": "success", "message": "LLM 响应完成"},
                duration_ms=llm_duration_ms,
                result_status="success",
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                total_tokens=usage["total_tokens"],
                cost_usd=usage["cost_usd"],
                metrics={"pricing_configured": usage["pricing_configured"]},
            )
        except Exception:
            pass
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
                if task_id is not None:
                    complete_generation_step(task_id, "待写入内容已生成")
                    preview_step_id = start_tool_step(
                        task_id=task_id,
                        tool_name="preview_word_diff",
                        arguments={"target_file": target_file, "operation_type": "append"},
                    )
                    if preview_step_id is None:
                        return {
                            "answer": "Workflow Plan 缺少 Word Diff Step，已停止写入流程。",
                            "tool_calls": executed_calls,
                            "status": "workflow_plan_mismatch",
                        }
                action_result = create_pending_action(
                    session_id=session_id,
                    target_file=target_file,
                    student_id=identity["student_id"],
                    student_name=identity["student_name"],
                    content=normalized_answer,
                    task_id=task_id,
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
            "get_top_three_students": 3,
            "query_table": 3,
            "retrieve_document": 3,
            "evaluate_scholarship_eligibility": 4,
        }
        ordered_calls = sorted(
            normalized_calls,
            key=lambda call: tool_priority.get(call["function"]["name"], 4),
        )
        for tool_call in ordered_calls:
            name = tool_call["function"]["name"]
            started = time.perf_counter()
            step_id = None
            if task_id is not None:
                preview_arguments = _argument_object(tool_call["function"]["arguments"])
                step_id = start_tool_step(
                    task_id=task_id, tool_name=name, arguments=preview_arguments
                )
            if task_id is not None and step_id is None:
                arguments = _argument_object(tool_call["function"]["arguments"])
                result = _invalid_tool_result(
                    "TOOL_NOT_IN_PLAN",
                    f"Workflow Plan 未声明 Tool：{name}，本次未执行",
                )
                retry_count = 0
            else:
                arguments, result, retry_count = _execute_tool_with_retry(
                    name,
                    tool_call["function"]["arguments"],
                    executed_calls,
                    {
                        "session_id": session_id,
                        "task_id": task_id,
                        "step_id": step_id,
                    },
                )
            duration_ms = max(0, round((time.perf_counter() - started) * 1000))
            executed_calls.append(
                {
                    "name": name,
                    "arguments": arguments,
                    "result": result,
                    "retry_count": retry_count,
                }
            )
            if task_id is not None and step_id is not None:
                record_tool_execution(
                    task_id=task_id,
                    tool_name=name,
                    arguments=arguments,
                    result=result,
                    retry_count=retry_count,
                    step_id=step_id,
                    duration_ms=duration_ms,
                )
            else:
                try:
                    record_trace(
                        session_id=session_id,
                        event_type="tool_execution",
                        tool_name=name,
                        arguments=arguments,
                        result=result,
                        duration_ms=duration_ms,
                        retry_count=retry_count,
                        result_status="success" if result.get("ok") is True else "failed",
                        error_code=None if result.get("ok") is True else result.get("error_code"),
                    )
                except Exception:
                    pass
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


def _current_step_id(task_id: str | None) -> str | None:
    if task_id is None:
        return None
    task = database.get_task_record(task_id)
    if task is None or task.get("current_step") is None:
        return None
    return next(
        (
            step["step_id"]
            for step in database.get_task_step_records(task_id)
            if step["sequence"] == task["current_step"]
        ),
        None,
    )


async def run_agent(
    message: str,
    client: ChatClient | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
    session_id: str = "direct",
) -> dict[str, Any]:
    """Run one Agent request and persist both sides of the chat."""
    context_resolution = resolve_message(session_id, message)
    resolved_message = context_resolution["message"]
    plan = create_plan(resolved_message)
    task = (
        start_task(session_id=session_id, user_message=message, plan=plan)
        if plan is not None
        else None
    )
    task_id = task["task_id"] if task is not None else None
    database.save_chat_message(
        session_id=session_id,
        role="user",
        content=message,
        used_tools=[],
        status="received",
    )
    if context_resolution["clarification"]:
        result = {
            "answer": context_resolution["clarification"],
            "tool_calls": [],
            "status": "clarification_required",
            "evidence": [],
        }
        database.save_chat_message(
            session_id=session_id,
            role="assistant",
            content=result["answer"],
            used_tools=[],
            status=result["status"],
        )
        update_after_run(session_id=session_id, result=result, task_id=None)
        return result
    try:
        result = await _run_agent_core(
            message=resolved_message,
            session_id=session_id,
            client=client,
            max_tool_rounds=max_tool_rounds,
            task_id=task_id,
            context_prompt=context_resolution["context_prompt"],
        )
    except Exception:
        if task_id is not None:
            finalize_task(
                task_id,
                {"status": "runtime_error", "answer": "请求处理失败"},
            )
        database.save_chat_message(
            session_id=session_id,
            role="assistant",
            content="请求处理失败",
            used_tools=[],
            status="error",
        )
        raise

    if task_id is not None:
        finalize_task(task_id, result)
        result["task_id"] = task_id

    result["evidence"] = _collect_evidence(result.get("tool_calls") or [])
    update_after_run(session_id=session_id, result=result, task_id=task_id)

    database.save_chat_message(
        session_id=session_id,
        role="assistant",
        content=result["answer"],
        used_tools=redact_value(result.get("tool_calls", [])),
        status=result["status"],
    )
    return result


def _collect_evidence(executed_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose only actual Tool provenance, deduplicated by evidence_id."""
    authoritative = [
        ((call.get("result") or {}).get("data") or {}).get("evidence_chain")
        for call in executed_calls
        if isinstance((call.get("result") or {}).get("data"), dict)
        and isinstance(
            ((call.get("result") or {}).get("data") or {}).get("evidence_chain"),
            list,
        )
        and isinstance(
            ((call.get("result") or {}).get("data") or {}).get("used_tools"),
            list,
        )
    ]
    # A conclusion Tool explicitly declares the Evidence it actually used. In
    # that case earlier retrieval candidates must not leak into answer sources.
    if authoritative:
        candidates_by_call = [authoritative[-1]]
    else:
        candidates_by_call = []
        for call in executed_calls:
            result = call.get("result") or {}
            candidates = result.get("evidence_chain")
            if candidates is None and isinstance(result.get("data"), dict):
                candidates = result["data"].get("evidence_chain") or result["data"].get("evidence")
            if candidates is None:
                candidates = result.get("evidence")
            if isinstance(candidates, list):
                candidates_by_call.append(candidates)
    collected: dict[str, dict[str, Any]] = {}
    for candidates in candidates_by_call:
        for item in candidates:
            if not isinstance(item, dict) or not item.get("evidence_id"):
                continue
            collected[str(item["evidence_id"])] = dict(item)
    return list(collected.values())
