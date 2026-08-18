"""Bounded, structured session context and deterministic pronoun resolution."""

import json
import re
from typing import Any

from backend import database
from backend.services.redaction import redacted_json, redact_text


MAX_CONTEXT_PROMPT_CHARS = 4000
MAX_STEP_SUMMARIES = 12
MAX_EVIDENCE_REFS = 20
PRONOUN_PATTERN = re.compile(r"(?:^|[，。！？\s])(?:那)?(?:他|她|该学生|这个学生)(?:的|呢|怎么样|如何|$)")
STUDENT_ID_PATTERN = re.compile(r"\bS\d+\b", re.I)


def resolve_message(session_id: str, message: str) -> dict[str, Any]:
    """Resolve only an unambiguous student pronoun; never pick a candidate."""
    state = get_context(session_id)
    explicit_ids = [item.upper() for item in STUDENT_ID_PATTERN.findall(message)]
    if explicit_ids:
        selected = next(
            (
                candidate for candidate in state["ambiguity_candidates"]
                if str(candidate.get("student_id", "")).upper() == explicit_ids[0]
            ),
            None,
        )
        if selected is not None:
            state["current_student"] = _student_ref(selected)
            state["ambiguity_candidates"] = []
            save_context(state)

    uses_pronoun = bool(PRONOUN_PATTERN.search(message))
    if uses_pronoun and state["ambiguity_candidates"]:
        return {
            "message": message,
            "context_prompt": build_context_prompt(state),
            "clarification": _ambiguity_text(state["ambiguity_candidates"]),
            "state": state,
        }
    resolved = message
    if uses_pronoun and state.get("current_student"):
        student = state["current_student"]
        resolved = (
            f"{message}\n"
            f"[结构化会话上下文：代词指向已确认学生 {student['student_id']} "
            f"{student.get('name') or ''}]"
        )
    return {
        "message": resolved,
        "context_prompt": build_context_prompt(state),
        "clarification": None,
        "state": state,
    }


def get_context(session_id: str) -> dict[str, Any]:
    stored = database.get_session_context_record(str(session_id))
    if stored is not None:
        return stored
    return {
        "session_id": str(session_id),
        "current_task_id": None,
        "current_student": None,
        "current_file": None,
        "ambiguity_candidates": [],
        "pending_action_id": None,
        "successful_steps": [],
        "evidence_refs": [],
        "session_summary": "",
        "updated_at": database.utc_now(),
    }


def save_context(state: dict[str, Any]) -> None:
    normalized = {
        **state,
        "successful_steps": list(state.get("successful_steps") or [])[-MAX_STEP_SUMMARIES:],
        "evidence_refs": list(state.get("evidence_refs") or [])[-MAX_EVIDENCE_REFS:],
        "session_summary": redact_text(str(state.get("session_summary") or ""))[:1000],
        "updated_at": database.utc_now(),
    }
    database.save_session_context_record(normalized)


def set_pending_action(session_id: str, action_id: str | None) -> None:
    state = get_context(session_id)
    state["pending_action_id"] = action_id
    save_context(state)


def clear_pending_action(session_id: str, action_id: str) -> None:
    state = get_context(session_id)
    if state.get("pending_action_id") == action_id:
        state["pending_action_id"] = None
        save_context(state)


def update_after_run(
    *,
    session_id: str,
    result: dict[str, Any],
    task_id: str | None,
) -> dict[str, Any]:
    state = get_context(session_id)
    state["current_task_id"] = task_id
    calls = result.get("tool_calls") or []
    for call in calls:
        data = (call.get("result") or {}).get("data")
        if call.get("name") == "search_student" and isinstance(data, dict):
            if data.get("status") == "found" and isinstance(data.get("student"), dict):
                state["current_student"] = _student_ref(data["student"])
                state["ambiguity_candidates"] = []
            elif data.get("status") == "ambiguous":
                state["current_student"] = None
                state["ambiguity_candidates"] = [
                    _student_ref(item) for item in (data.get("candidates") or [])[:20]
                ]
        elif call.get("name") in {
            "get_student_info", "get_student_scores", "get_student_research"
        } and isinstance(data, dict):
            if data.get("student_id") and data.get("status") != "not_found":
                previous = state.get("current_student") or {}
                state["current_student"] = {
                    **previous,
                    **_student_ref(data),
                    "student_id": data["student_id"],
                }
                state["ambiguity_candidates"] = []
        file_ref = _file_ref(call)
        if file_ref is not None:
            state["current_file"] = file_ref

    pending = result.get("pending_action") or {}
    state["pending_action_id"] = pending.get("action_id")
    state["evidence_refs"] = _evidence_refs(result.get("evidence") or [])
    if task_id:
        state["successful_steps"] = [
            {
                "sequence": step["sequence"],
                "step_name": step["step_name"],
                "tool_name": step.get("tool_name"),
                "result_summary": redact_text(str(step.get("result_summary") or ""))[:240],
            }
            for step in database.get_task_step_records(task_id)
            if step["status"] == "success"
        ][-MAX_STEP_SUMMARIES:]
    else:
        state["successful_steps"] = []
    state["session_summary"] = (
        f"最近任务状态：{result.get('status', 'unknown')}；"
        f"最近使用工具：{', '.join(str(call.get('name')) for call in calls[-6:]) or '无'}"
    )
    save_context(state)
    return state


def build_context_prompt(state: dict[str, Any]) -> str:
    """Return a bounded structured summary, never raw chat or full Tool results."""
    compact = {
        "current_student": state.get("current_student"),
        "current_file": state.get("current_file"),
        "ambiguity_candidates": state.get("ambiguity_candidates") or [],
        "pending_action_id": state.get("pending_action_id"),
        "successful_steps": state.get("successful_steps") or [],
        "evidence_refs": state.get("evidence_refs") or [],
        "session_summary": state.get("session_summary") or "",
    }
    serialized = redacted_json(compact, max_length=MAX_CONTEXT_PROMPT_CHARS)
    return (
        "以下是有界结构化会话状态，不是事实来源。学生事实仍须调用工具确认；"
        "如 ambiguity_candidates 非空，禁止用代词选择候选：\n" + serialized
    )


def _student_ref(student: dict[str, Any]) -> dict[str, Any]:
    return {
        key: student.get(key)
        for key in ("student_id", "name", "major", "college", "grade", "class_name")
        if student.get(key) is not None
    }


def _file_ref(call: dict[str, Any]) -> dict[str, Any] | None:
    arguments = call.get("arguments") or {}
    result = call.get("result") or {}
    file_id = arguments.get("file_id")
    evidence = result.get("evidence_chain") or result.get("evidence") or []
    if isinstance(evidence, dict):
        evidence = []
    first = next((item for item in evidence if isinstance(item, dict)), None)
    file_id = file_id or (first or {}).get("file_id")
    if not file_id:
        return None
    return {"file_id": file_id, "file_name": (first or {}).get("file_name")}


def _evidence_refs(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = []
    for item in evidence[:MAX_EVIDENCE_REFS]:
        refs.append(
            {
                key: item.get(key)
                for key in (
                    "evidence_id", "source_type", "file_id", "file_name", "sheet",
                    "page_no", "chunk_id", "field", "record_key",
                )
                if item.get(key) is not None
            }
        )
    return refs


def _ambiguity_text(candidates: list[dict[str, Any]]) -> str:
    name = candidates[0].get("name", "该姓名") if candidates else "该姓名"
    lines = [f"当前上下文中找到多名姓名为{name}的学生，请先确认学号：", ""]
    for index, candidate in enumerate(candidates, start=1):
        details = "，".join(
            str(candidate.get(key))
            for key in ("major", "college", "grade", "class_name")
            if candidate.get(key)
        )
        suffix = f"，{details}" if details else ""
        lines.append(
            f"{index}. {candidate.get('student_id')} {candidate.get('name')}{suffix}"
        )
    return "\n".join(lines)
