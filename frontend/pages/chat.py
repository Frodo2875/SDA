"""Chat Workspace composed from existing messages, Evidence and approvals."""

from typing import Any
import streamlit as st
from frontend import api_client, chat_workspace as workspace, controller as actions
from frontend.components.chat_panel import render_messages
from frontend.components.evidence_panel import render_evidence_preview


def render_context() -> None:
    st.caption(f"当前知识库：{st.session_state.get('knowledge_base_name') or '全部文档'}")
    if st.session_state.get("knowledge_base_id"):
        st.info("已携带知识库上下文；当前检索范围尚未限定到该知识库。")
    with st.expander("选择知识库 / 新建会话"):
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
    workspace.save_conversation()
    with st.expander("当前知识库历史聊天"):
        st.caption("仅保留当前浏览器会话内的聊天；断开会话或重启后可能丢失，不是服务器历史记录。")
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
        st.caption(f"Research Task · {message['research_task_id']}")
        st.write(f"状态：{task.get('status', '待刷新')} · 阶段：{(task.get('progress') or {}).get('stage', '待刷新')}")
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
            st.caption("研究完成后可生成报告草稿；写入文件仍需审批。")
            if not message.get("report") and st.button("生成报告草稿", key=f"report-create-{index}", disabled=task.get("status") != "COMPLETED"):
                workspace.generate_report(message)
                st.rerun()
        report = message.get("report")
        if report:
            st.subheader(report["title"])
            st.markdown(report["summary"])
            st.caption(f"报告 ID：{report['report_id']} · 草稿（导出状态以审批结果为准）")
            for warning in report.get("warnings") or []:
                st.warning(str(warning.get("message") or warning) if isinstance(warning, dict) else str(warning))
            format = st.selectbox("导出格式", ["md", "docx"], key=f"report-format-{index}")
            if st.button("预览并申请导出", key=f"report-export-{index}"):
                try:
                    actions.add_version_action(api_client.preview_report(message["research_task_id"], report["report_id"], format))
                except RuntimeError as exc:
                    st.error(str(exc))


def render() -> None:
    st.title("聊天")
    render_context()
    st.radio("聊天模式", workspace.MODES, key="chat_mode", horizontal=True)
    conversation, evidence = st.columns([2, 1], gap="large")
    with conversation:
        st.subheader("Agent Chat")
        if not st.session_state.messages:
            st.info("开始提问，例如：综合分析 S001。")
        render_messages(st.session_state.messages, actions.handle_action, actions.handle_evidence_location)
        for index, message in enumerate(st.session_state.messages):
            render_research(message, index)
    with evidence:
        render_evidence_preview(
            st.session_state.latest_evidence,
            location=st.session_state.selected_evidence_location,
            location_error=st.session_state.evidence_location_error,
            on_locate=actions.handle_evidence_location,
        )
    workspace.save_conversation()
    if prompt := st.chat_input("输入问题或研究目标", key="workspace-chat"):
        if prompt.strip():
            workspace.submit(prompt.strip())
            st.rerun()
