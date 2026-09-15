"""Shared session state and existing HTTP callbacks for the V7 pages."""

from typing import Any
from uuid import uuid4

import streamlit as st

from frontend import api_client
from frontend.components.chat_panel import comparison_tables, tool_statuses
from frontend.components.file_panel import (
    FILE_TYPE_FILTERS,
    filter_files_by_lifecycle,
)


def initialize_state() -> None:
    defaults = {
        "active_page": "dashboard",
        "chat_mode": "普通问答",
        "workspace_name": "默认 Workspace",
        "session_id": uuid4().hex,
        "messages": [],
        "files": [],
        "files_loaded": False,
        "files_error": None,
        "upload_notice": [],
        "uploader_version": 0,
        "workspace_search": "",
        "workspace_file_type": "全部类型",
        "workspace_lifecycle": "全部状态",
        "workspace_sort": "登记时间（新到旧）",
        "workspace_selected_file_id": None,
        "workspace_preview_file_id": None,
        "known_tasks": {},
        "latest_evidence": [],
        "selected_evidence_location": None,
        "evidence_location_error": None,
        "show_document_workspace": True,
        "show_insight_panel": True,
    }
    for key, value in defaults.items():
        st.session_state[key] = st.session_state[key] if key in st.session_state else value


def refresh_files() -> None:
    try:
        sorting = {
            "登记时间（新到旧）": ("created_time", "desc"),
            "登记时间（旧到新）": ("created_time", "asc"),
            "文件大小（大到小）": ("size", "desc"),
            "文件大小（小到大）": ("size", "asc"),
        }
        sort_by, sort_order = sorting[st.session_state.workspace_sort]
        files = api_client.list_files(
            search=st.session_state.workspace_search,
            file_type=FILE_TYPE_FILTERS[st.session_state.workspace_file_type],
            lifecycle_status=None,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        st.session_state.files = filter_files_by_lifecycle(
            files, st.session_state.workspace_lifecycle
        )
        st.session_state.files_error = None
    except RuntimeError as exc:
        st.session_state.files = []
        st.session_state.files_error = str(exc)
    st.session_state.files_loaded = True


def upload_files(uploaded_files: list[Any]) -> None:
    with st.spinner(f"正在上传并处理 {len(uploaded_files)} 个文档…"):
        st.session_state.upload_notice = api_client.upload_files(uploaded_files)
    st.session_state.uploader_version += 1
    refresh_files()
    st.rerun()


def refresh_tasks() -> None:
    try:
        persisted = api_client.list_tasks(st.session_state.session_id)
        for item in persisted:
            task = item.get("task") or item
            if task.get("task_id"):
                st.session_state.known_tasks[task["task_id"]] = item
    except RuntimeError:
        pass
    for task_id, previous in list(st.session_state.known_tasks.items()):
        try:
            st.session_state.known_tasks[task_id] = api_client.get_task(task_id)
        except RuntimeError:
            st.session_state.known_tasks[task_id] = previous


def handle_task_operation(task_id: str, operation: str) -> None:
    actions = {
        "cancel": api_client.cancel_task,
        "retry": api_client.retry_task,
        "resume": api_client.resume_task,
    }
    try:
        result = actions[operation](task_id)
        data = result.get("data") or {}
        if data:
            st.session_state.known_tasks[task_id] = data
        refresh_tasks()
    except RuntimeError as exc:
        st.session_state.messages.append(
            {"role": "assistant", "content": f"任务操作失败：{exc}", "error": True}
        )
    st.rerun()


def submit_message(message: str) -> None:
    st.session_state.latest_evidence = []
    st.session_state.selected_evidence_location = None
    st.session_state.evidence_location_error = None
    st.session_state.messages.append({"role": "user", "content": message})
    try:
        with st.spinner("Agent 正在处理材料…"):
            response = api_client.chat(st.session_state.session_id, message)
        task_id = response.get("task_id")
        task = None
        display_warnings = []
        if task_id:
            try:
                task = api_client.get_task(task_id)
            except RuntimeError:
                display_warnings.append("任务详情暂不可用，可稍后刷新。")
        if task_id and task:
            st.session_state.known_tasks[task_id] = task
        try:
            traces = api_client.get_traces(task_id=task_id, session_id=None if task_id else st.session_state.session_id)
        except RuntimeError:
            traces = []
            display_warnings.append("Trace 暂不可用，回答已保留。")
        displayed_evidence = (
            response.get("unified_evidence") or response.get("evidence") or []
        )
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": response.get("answer") or "任务已处理。",
                "statuses": tool_statuses(response),
                "comparison_tables": comparison_tables(response),
                "pending_action": response.get("pending_action"),
                "evidence": displayed_evidence,
                "task": task,
                "traces": traces,
                "display_warnings": display_warnings,
                "evidence_quality": response.get("evidence_quality"),
                "web_search_warnings": response.get("web_search_warnings"),
                "research_task_id": response.get("task_id") if response.get("status") == "research_task_created" else None,
            }
        )
        st.session_state.latest_evidence = displayed_evidence
    except RuntimeError as exc:
        st.session_state.messages.append(
            {"role": "assistant", "content": f"请求失败：{exc}", "error": True}
        )


def handle_action(message_index: int, decision: str) -> None:
    message = st.session_state.messages[message_index]
    action = message.get("pending_action") or {}
    action_id = action.get("action_id")
    if not action_id or action.get("status") != "pending":
        return
    try:
        result = api_client.decide_action(action_id, decision)
        if decision == "confirm":
            data = result.get("data") or {}
            updated = data.get("pending_action") or {}
            message["pending_action"] = {**action, **updated, "status": "executed"}
            write_result = data.get("write_result") or {}
            new_version = (write_result.get("data") or {}).get("new_version") or {}
            is_delete = action.get("action_type") == "delete_file"
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": "文件已确认删除。" if is_delete else "操作已确认并执行。",
                    "version_id": new_version.get("version_id"),
                }
            )
        else:
            updated = result.get("data") or {}
            message["pending_action"] = {**action, **updated, "status": "cancelled"}
            st.session_state.messages.append(
                {"role": "assistant", "content": "已取消，目标文件没有变化。"}
            )
        refresh_files()
        refresh_tasks()
    except RuntimeError as exc:
        st.session_state.messages.append(
            {"role": "assistant", "content": f"操作失败：{exc}", "error": True}
        )
    st.rerun()


def add_version_action(result: dict[str, Any]) -> None:
    st.session_state.active_page = "chat"
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": result.get("message") or "版本操作等待确认。",
            "pending_action": result.get("data") or {},
        }
    )
    st.rerun()


def handle_evidence_location(evidence: dict[str, Any]) -> None:
    """Open one cited source without changing the answer or its Evidence list."""
    try:
        st.session_state.selected_evidence_location = api_client.locate_evidence(
            evidence["evidence_id"], st.session_state.session_id
        )
        st.session_state.evidence_location_error = None
    except (KeyError, RuntimeError):
        st.session_state.selected_evidence_location = None
        st.session_state.evidence_location_error = (
            "来源存在，但当前无法打开对应预览位置"
        )
    st.rerun()
