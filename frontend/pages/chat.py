"""Chat Workspace composed from existing messages, Evidence and approvals."""

from typing import Any
import streamlit as st
from frontend.presentation import label as status_label
from frontend import api_client, chat_workspace as workspace, controller as actions
from frontend.components.chat_panel import render_messages
from frontend.components.evidence_panel import render_evidence_preview
from frontend.components.document_actions import render_document_actions


def render_context() -> None:
    st.caption(f"当前知识库：{st.session_state.get('knowledge_base_name') or '全部文档'}")
    if st.session_state.get("knowledge_base_id"):
        st.info("回答可能参考其他知识库的资料。")


def render_new_chat() -> None:
    with st.expander("新建聊天"):
        if st.button("加载知识库", key="chat-load-knowledge"):
            try:
                st.session_state.chat_knowledge_choices = api_client.list_knowledge_bases()
                st.session_state.pop("chat_knowledge_error", None)
            except RuntimeError as exc:
                st.session_state.chat_knowledge_error = str(exc)
        if st.session_state.get("chat_knowledge_error"):
            st.warning(st.session_state.chat_knowledge_error)
        choices = {None: "全部文档", **{item["knowledge_base_id"]: item["name"] for item in st.session_state.get("chat_knowledge_choices", [])}}
        current = st.session_state.get("knowledge_base_id")
        if current and current not in choices:
            choices[current] = st.session_state.get("knowledge_base_name") or current
        selected = st.selectbox("知识库", list(choices), index=list(choices).index(current), format_func=lambda key: choices[key])
        if st.button("开始新会话", key="chat-new"):
            workspace.new_conversation(selected, choices[selected])
            st.rerun()


def render_recent_chat() -> None:
    workspace.save_conversation()
    with st.expander("最近聊天"):
        st.caption("记录仅在本次浏览器会话中保留。")
        history = workspace.history_for_knowledge()
        if not history:
            st.caption("暂无历史会话。")
        for item in history:
            st.text(item["title"])
            st.caption(f"创建：{item['created_at']} · 更新：{item['updated_at']}")
            st.text(item["messages"][-1]["content"][:120])
            if st.button("打开会话", key=f"history-{item['session_id']}", disabled=item["session_id"] == st.session_state.session_id):
                workspace.restore_conversation(item["session_id"])
                st.rerun()


def render_research(message: dict[str, Any], index: int) -> None:
    if not message.get("research_task_id"):
        return
    with st.container(border=True):
        task = message.get("research") or {}
        st.caption(f"研究任务 · {message['research_task_id']}")
        if st.button("在任务中心打开", key=f"research-center-open-{index}"):
            st.session_state.research_selected_id = message["research_task_id"]
            st.session_state.active_page = "tasks"
            st.rerun()
        st.write(f"状态：{status_label(task.get('status', '待刷新'))} · 阶段：{status_label((task.get('progress') or {}).get('stage', '待刷新'))}")
        if task.get("updated_at"):
            st.caption(f"更新时间：{task['updated_at']}")
        if (task.get("error") or {}).get("error_message"):
            st.error(task["error"]["error_message"])
        for key in ("research_error", "trace_error", "report_error"):
            if message.get(key):
                st.warning(message[key])
        if st.button("刷新研究状态", key=f"research-refresh-{index}"):
            workspace.refresh_research(message)
            st.rerun()
        if message.get("report_requested"):
            st.caption("研究完成后可生成报告，导出前需要确认。")
            if not message.get("report") and st.button("生成报告草稿", key=f"report-create-{index}", disabled=task.get("status") != "COMPLETED"):
                workspace.generate_report(message)
                st.rerun()
        report = message.get("report")
        if report:
            st.subheader(report["title"])
            st.markdown(report["summary"])
            st.caption(f"报告 ID：{report['report_id']} · 草稿")
            for warning in report.get("warnings") or []:
                st.warning(str(warning.get("message") or warning) if isinstance(warning, dict) else str(warning))
            format = st.selectbox("导出格式", ["md", "docx"], key=f"report-format-{index}")
            if st.button("预览并申请导出", key=f"report-export-{index}"):
                try:
                    actions.add_version_action(api_client.preview_report(message["research_task_id"], report["report_id"], format))
                except RuntimeError as exc:
                    st.error(str(exc))


def render() -> None:
    st.title("欢迎来到SDA！", text_alignment="center")
    render_context()
    if st.button("收起" if st.session_state.show_chat_tools else "更多",
                 key="chat-more", use_container_width=True):
        st.session_state.show_chat_tools = not st.session_state.show_chat_tools
        st.rerun()
    show_sources = bool(st.session_state.get("chat-show-sources", False))
    show_details = bool(st.session_state.get("chat-show-details", False))
    if st.session_state.show_chat_tools:
        first_left, first_right = st.columns(2, gap="small")
        with first_left:
            render_new_chat()
        with first_right:
            render_recent_chat()
        second_left, second_right = st.columns(2, gap="small")
        with second_left:
            render_document_actions()
        with second_right:
            with st.expander("显示选项"):
                show_sources = st.toggle("查看参考资料", key="chat-show-sources", value=False)
                show_details = st.toggle("查看处理详情", key="chat-show-details", value=False)
    if show_sources:
        conversation, evidence = st.columns([2, 1], gap="large")
    else:
        conversation, evidence = st.container(), None
    with conversation:
        render_messages(st.session_state.messages, actions.handle_action,
                        actions.handle_evidence_location, show_sources=show_sources,
                        show_details=show_details)
        for index, message in enumerate(st.session_state.messages):
            render_research(message, index)
    if evidence is not None:
        with evidence:
            render_evidence_preview(
                st.session_state.latest_evidence,
                location=st.session_state.selected_evidence_location,
                location_error=st.session_state.evidence_location_error,
                on_locate=actions.handle_evidence_location,
            )
    workspace.save_conversation()
    mode_column, input_column = st.columns([1.15, 6], gap="small", vertical_alignment="bottom")
    with mode_column:
        st.selectbox("聊天模式", workspace.MODES, key="chat_mode",
                     label_visibility="collapsed", help="选择普通问答、深度研究或报告生成")
    with input_column:
        prompt = st.chat_input("输入问题或研究目标", key="workspace-chat")
    if prompt:
        if prompt.strip():
            workspace.submit(prompt.strip())
            st.rerun()
