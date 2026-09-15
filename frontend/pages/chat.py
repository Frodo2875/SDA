"""Chat page: original messages, approvals, Trace and Evidence components."""

import streamlit as st
from frontend import controller as actions
from frontend.components.chat_panel import render_messages
from frontend.components.evidence_panel import render_evidence_preview


def render() -> None:
    st.title("聊天")
    st.caption("在当前会话中提问，查看资料引用与研究结果。")
    conversation, evidence = st.columns([2, 1], gap="large")
    with conversation:
        st.subheader("Agent Chat")
        if not st.session_state.messages:
            st.info("开始提问，例如：综合分析 S001。")
        render_messages(st.session_state.messages, actions.handle_action, actions.handle_evidence_location)
        if prompt := st.chat_input("输入问题，例如：那他的科研呢？", key="workspace-chat"):
            if prompt.strip():
                actions.submit_message(prompt.strip())
                st.rerun()
    with evidence:
        render_evidence_preview(
            st.session_state.latest_evidence,
            location=st.session_state.selected_evidence_location,
            location_error=st.session_state.evidence_location_error,
            on_locate=actions.handle_evidence_location,
        )
