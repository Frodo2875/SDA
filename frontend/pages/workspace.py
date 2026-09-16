"""Legacy workspace, retained in the dashboard quick-access tab."""

import streamlit as st
from frontend.controller import (
    refresh_files, upload_files, refresh_tasks, handle_task_operation,
    submit_message, handle_action, add_version_action, handle_evidence_location,
)
from frontend.components.file_panel import render_file_panel
from frontend.components.chat_panel import render_messages
from frontend.components.evidence_panel import render_evidence_preview
from frontend.components.task_center import render_task_center


def render() -> None:
    visibility_controls = st.columns([1, 1, 4], gap="small")
    visibility_controls[0].toggle("显示左栏", key="show_document_workspace")
    visibility_controls[1].toggle("显示右栏", key="show_insight_panel")

    show_workspace = st.session_state.show_document_workspace
    show_insight = st.session_state.show_insight_panel
    workspace_column = None
    insight_column = None
    if show_workspace and show_insight:
        workspace_column, chat_column, insight_column = st.columns(
            [1.15, 2.0, 1.15], gap="large"
        )
    elif show_workspace:
        workspace_column, chat_column = st.columns([1.15, 3.15], gap="large")
    elif show_insight:
        chat_column, insight_column = st.columns([3.15, 1.15], gap="large")
    else:
        chat_column = st.container()

    if workspace_column is not None:
        with workspace_column:
            render_file_panel(
                files=st.session_state.files,
                session_id=st.session_state.session_id,
                uploader_version=st.session_state.uploader_version,
                upload_notice=st.session_state.upload_notice,
                files_error=st.session_state.files_error,
                on_upload=upload_files,
                on_refresh=lambda: (refresh_files(), st.rerun()),
                on_version_action=add_version_action,
            )

    with chat_column:
        st.subheader("聊天")
        if not st.session_state.messages:
            with st.chat_message("assistant", avatar="🎓"):
                st.markdown("欢迎使用。你可以尝试输入：`综合分析 S001`。")
        render_messages(st.session_state.messages, handle_action, handle_evidence_location,
                        show_sources=show_insight, show_details=show_insight)
        if prompt := st.chat_input("输入问题，例如：那他的科研呢？", key="workspace-chat"):
            submit_message(prompt.strip())
            st.rerun()

    if insight_column is not None:
        with insight_column:
            render_evidence_preview(
                st.session_state.latest_evidence,
                location=st.session_state.selected_evidence_location,
                location_error=st.session_state.evidence_location_error,
                on_locate=handle_evidence_location,
            )
            st.divider()
            render_task_center(
                list(st.session_state.known_tasks.values()),
                on_refresh=lambda: (refresh_tasks(), st.rerun()),
                on_cancel=lambda task_id: handle_task_operation(task_id, "cancel"),
                on_retry=lambda task_id: handle_task_operation(task_id, "retry"),
                on_resume=lambda task_id: handle_task_operation(task_id, "resume"),
            )
