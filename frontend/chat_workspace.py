"""Browser conversation state and HTTP orchestration; no Agent or retrieval logic."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import streamlit as st

from frontend import api_client, controller

MODES = ["普通问答", "深度研究", "报告生成"]
SESSION_FIELDS = ("session_id", "messages", "latest_evidence", "selected_evidence_location",
                  "evidence_location_error", "known_tasks", "knowledge_base_id", "knowledge_base_name", "chat_mode")


def save_conversation() -> None:
    history = st.session_state.setdefault("chat_history", {})
    if not st.session_state.get("messages"):
        return
    identifier = st.session_state.session_id
    previous = history.get(identifier, {})
    snapshot = {key: deepcopy(st.session_state.get(key)) for key in SESSION_FIELDS}
    now = datetime.now(timezone.utc).isoformat()
    messages = snapshot["messages"]
    snapshot["title"] = next((item["content"][:50] for item in messages if item["role"] == "user"), "会话")
    snapshot["created_at"] = previous.get("created_at", now)
    changed = previous.get("messages") != messages
    snapshot["updated_at"] = now if changed else previous.get("updated_at", now)
    history[identifier] = snapshot


def new_conversation(knowledge_base_id: str | None = None, name: str = "全部文档") -> None:
    save_conversation()
    st.session_state.update(session_id=uuid4().hex, messages=[], latest_evidence=[],
                            selected_evidence_location=None, evidence_location_error=None, known_tasks={},
                            knowledge_base_id=knowledge_base_id, knowledge_base_name=name, chat_mode=MODES[0])


def restore_conversation(identifier: str) -> None:
    save_conversation()
    snapshot = st.session_state.chat_history[identifier]
    for key in SESSION_FIELDS:
        st.session_state[key] = deepcopy(snapshot.get(key))


def history_for_knowledge() -> list[dict[str, Any]]:
    identifier = st.session_state.get("knowledge_base_id")
    return sorted([item for item in st.session_state.get("chat_history", {}).values()
                   if item.get("knowledge_base_id") == identifier],
                  key=lambda item: item["updated_at"], reverse=True)


def submit(prompt: str) -> None:
    mode = st.session_state.get("chat_mode") or MODES[0]
    if mode == MODES[0]:
        controller.submit_message(prompt)
        # The existing Chat API may itself route a complex query to a task.
        message = st.session_state.messages[-1]
        task = (message.get("task") or {}).get("task") or {}
        if task.get("task_type") == "research":
            message["research_task_id"] = task["task_id"]
    else:
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.session_state.latest_evidence = []
        st.session_state.selected_evidence_location = None
        st.session_state.evidence_location_error = None
        try:
            task = api_client.create_research_task(st.session_state.session_id, prompt)
            st.session_state.messages.append({"role": "assistant", "content": "研究任务已创建，可刷新查看执行状态。",
                                             "research_task_id": task["task_id"], "research": task,
                                             "report_requested": mode == MODES[2]})
        except RuntimeError as exc:
            st.session_state.messages.append({"role": "assistant", "content": str(exc), "error": True})
    save_conversation()


def refresh_research(message: dict[str, Any]) -> None:
    try:
        task = api_client.get_research_task(message["research_task_id"])
        message["research"] = task
        message.pop("research_error", None)
        result = task.get("result")
        if isinstance(result, dict):
            message["content"] = result.get("answer") or "研究结果已返回。"
            message["evidence"] = result.get("unified_evidence") or result.get("evidence") or []
            message["pending_action"] = result.get("pending_action")
            message["evidence_quality"] = result.get("evidence_quality")
            message["web_search_warnings"] = result.get("web_search_warnings")
            st.session_state.latest_evidence = message["evidence"]
            st.session_state.selected_evidence_location = None
        # Trace is ancillary; its failure must not discard a completed result.
        try:
            message["traces"] = api_client.get_traces(task_id=message["research_task_id"])
            message.pop("trace_error", None)
        except RuntimeError:
            message["trace_error"] = "Trace 暂不可用"
    except RuntimeError as exc:
        message["research_error"] = str(exc)
    save_conversation()


def generate_report(message: dict[str, Any]) -> None:
    try:
        message["report"] = api_client.create_report(message["research_task_id"], "证据研究报告")
        message.pop("report_error", None)
    except RuntimeError as exc:
        message["report_error"] = str(exc)
    save_conversation()
