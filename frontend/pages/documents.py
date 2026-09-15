"""Document page reuses the original upload and lifecycle controls."""

import streamlit as st
from frontend import controller as actions
from frontend.components.file_panel import render_file_panel


def render() -> None:
    st.title("我的文档")
    render_file_panel(
        files=st.session_state.files, session_id=st.session_state.session_id,
        uploader_version=st.session_state.uploader_version,
        upload_notice=st.session_state.upload_notice, files_error=st.session_state.files_error,
        on_upload=actions.upload_files,
        on_refresh=lambda: (actions.refresh_files(), st.rerun()),
        on_version_action=actions.add_version_action,
    )
