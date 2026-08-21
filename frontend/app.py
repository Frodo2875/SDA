"""Streamlit entry point composed from V2 HTTP-only frontend components."""

from typing import Any
from uuid import uuid4

import streamlit as st

from frontend import api_client
from frontend.components.chat_panel import comparison_tables, render_messages, tool_statuses
from frontend.components.file_panel import render_file_panel


def initialize_state() -> None:
    defaults = {
        "session_id": uuid4().hex,
        "messages": [],
        "files": [],
        "files_loaded": False,
        "files_error": None,
        "upload_notice": None,
        "uploader_version": 0,
        "workspace_search": "",
        "workspace_file_type": "全部类型",
        "workspace_lifecycle": "全部状态",
        "workspace_sort": "登记时间（新到旧）",
        "workspace_selected_file_id": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def refresh_files() -> None:
    try:
        file_types = {"全部类型": None, "Excel": "excel", "Word": "word", "PDF": "pdf"}
        lifecycle_states = {
            "全部状态": None,
            "已上传": "uploaded",
            "处理中": "processing",
            "可用": "ready",
            "失败": "failed",
            "已删除": "deleted",
            "清理失败": "cleanup_failed",
        }
        sorting = {
            "登记时间（新到旧）": ("created_time", "desc"),
            "登记时间（旧到新）": ("created_time", "asc"),
            "文件大小（大到小）": ("size", "desc"),
            "文件大小（小到大）": ("size", "asc"),
        }
        sort_by, sort_order = sorting[st.session_state.workspace_sort]
        st.session_state.files = api_client.list_files(
            search=st.session_state.workspace_search,
            file_type=file_types[st.session_state.workspace_file_type],
            lifecycle_status=lifecycle_states[st.session_state.workspace_lifecycle],
            sort_by=sort_by,
            sort_order=sort_order,
        )
        st.session_state.files_error = None
    except RuntimeError as exc:
        st.session_state.files = []
        st.session_state.files_error = str(exc)
    st.session_state.files_loaded = True


def upload_file(uploaded_file: Any) -> None:
    try:
        with st.spinner("正在解析文档…"):
            result = api_client.upload_file(uploaded_file)
        st.session_state.upload_notice = {
            "ok": True, "message": result.get("message") or "文件上传成功",
        }
        st.session_state.uploader_version += 1
        refresh_files()
    except RuntimeError as exc:
        st.session_state.upload_notice = {"ok": False, "message": str(exc)}
    st.rerun()


def submit_message(message: str) -> None:
    st.session_state.messages.append({"role": "user", "content": message})
    try:
        with st.spinner("Agent 正在处理材料…"):
            response = api_client.chat(st.session_state.session_id, message)
        task_id = response.get("task_id")
        task = api_client.get_task(task_id) if task_id else None
        traces = api_client.get_traces(
            task_id=task_id,
            session_id=None if task_id else st.session_state.session_id,
        )
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": response.get("answer") or "任务已处理。",
                "statuses": tool_statuses(response),
                "comparison_tables": comparison_tables(response),
                "pending_action": response.get("pending_action"),
                "evidence": response.get("evidence") or [],
                "task": task,
                "traces": traces,
            }
        )
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
            st.session_state.messages.append(
                {
                    "role": "assistant", "content": "操作已确认并执行。",
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
    except RuntimeError as exc:
        st.session_state.messages.append(
            {"role": "assistant", "content": f"操作失败：{exc}", "error": True}
        )
    st.rerun()


def add_version_action(result: dict[str, Any]) -> None:
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": result.get("message") or "版本操作等待确认。",
            "pending_action": result.get("data") or {},
        }
    )
    st.rerun()


st.set_page_config(
    page_title="学生材料智能文档助手", page_icon="🎓", layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    """
    <style>
    .block-container {max-width: 1040px; padding-top: 2rem; padding-bottom: 6rem;}
    [data-testid="stSidebar"] {border-right: 1px solid #e8edf3;}
    [data-testid="stSidebar"] .block-container {padding-top: 1.6rem;}
    div[data-testid="stChatMessage"] {border-radius: 14px; padding: 0.35rem 0.7rem;}
    .app-kicker {color: #55718f; font-size: 0.92rem; margin-bottom: 0.2rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

initialize_state()
if not st.session_state.files_loaded:
    refresh_files()

with st.sidebar:
    render_file_panel(
        files=st.session_state.files,
        session_id=st.session_state.session_id,
        uploader_version=st.session_state.uploader_version,
        upload_notice=st.session_state.upload_notice,
        files_error=st.session_state.files_error,
        on_upload=upload_file,
        on_refresh=lambda: (refresh_files(), st.rerun()),
        on_version_action=add_version_action,
    )

st.markdown('<p class="app-kicker">学生材料智能文档助手</p>', unsafe_allow_html=True)
st.title("你好，需要查询哪份学生材料？")
st.caption("支持结构化查询、文档 Evidence、任务进度、确认写入和版本恢复。")
if not st.session_state.messages:
    with st.chat_message("assistant", avatar="🎓"):
        st.markdown("欢迎使用。你可以尝试输入：`综合分析 S001`。")
render_messages(st.session_state.messages, handle_action)
if prompt := st.chat_input("输入问题，例如：那他的科研呢？"):
    submit_message(prompt.strip())
    st.rerun()
