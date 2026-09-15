"""Workspace overview with honest unavailable states and a legacy quick tab."""

import streamlit as st
from frontend import api_client
from frontend.pages import workspace
from frontend.shell import navigate


def refresh_overview() -> None:
    try:
        st.session_state.overview_files = api_client.list_files()
        st.session_state.overview_files_error = None
    except RuntimeError:
        st.session_state.overview_files = None
        st.session_state.overview_files_error = "文件概览暂不可用，请稍后刷新。"
    # Counts below deliberately describe known current-session tasks. They
    # aren't advertised as global backend totals.


def render() -> None:
    st.title("工作空间概览")
    overview, quick = st.tabs(["概览", "快捷工作台"])
    with overview:
        if "overview_files" not in st.session_state:
            refresh_overview()
        st.caption("从文档到研究结果，在一个工作空间内完成。")
        st.button("刷新概览", key="refresh-overview", on_click=refresh_overview)
        files = st.session_state.overview_files
        cards = st.columns(4)
        cards[0].metric("知识库数量", st.session_state.get("knowledge_count", "—"), help="最近一次打开知识库列表时的数量")
        cards[1].metric("文件数量", len(files) if files is not None else "—", help="现有文件列表，不受文档页筛选影响")
        cards[2].metric("任务数量", len(st.session_state.known_tasks), help="当前浏览器会话已加载的任务")
        cards[3].metric("报告数量", "—", help="报告列表尚未接入")
        if st.session_state.overview_files_error:
            st.warning(st.session_state.overview_files_error)
        st.write("")
        shortcuts = st.columns(3)
        shortcuts[0].button("开始聊天", on_click=navigate, args=("chat",), use_container_width=True)
        shortcuts[1].button("管理文档", on_click=navigate, args=("documents",), use_container_width=True)
        shortcuts[2].button("查看任务", on_click=navigate, args=("tasks",), use_container_width=True)
        st.markdown("### 最近活动")
        columns = st.columns(3)
        with columns[0], st.container(border=True):
            st.markdown("**最近聊天**")
            messages = [item for item in st.session_state.messages if item.get("role") == "user"][-3:]
            for item in reversed(messages):
                st.text(str(item.get("content", ""))[:160])
            if not messages:
                st.caption("当前会话还没有聊天记录。")
        with columns[1], st.container(border=True):
            st.markdown("**最近任务**")
            tasks = list(st.session_state.known_tasks.values())[-3:]
            for item in reversed(tasks):
                task = item.get("task") or item
                st.text(str(task.get("task_summary") or task.get("task_id") or "研究任务")[:120])
                st.caption(str(task.get("task_status") or task.get("status") or "—"))
            if not tasks:
                st.caption("当前会话尚未加载任务。")
        with columns[2], st.container(border=True):
            st.markdown("**最近报告**")
            st.caption("报告列表尚未接入。")
    with quick:
        workspace.render()
