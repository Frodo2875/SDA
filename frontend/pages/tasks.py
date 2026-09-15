"""Task page keeps the existing task center and action callbacks."""

import streamlit as st
from frontend import controller as actions
from frontend.components.task_center import render_task_center


def render() -> None:
    st.title("研究任务")
    render_task_center(
        list(st.session_state.known_tasks.values()),
        on_refresh=lambda: (actions.refresh_tasks(), st.rerun()),
        on_cancel=lambda task_id: actions.handle_task_operation(task_id, "cancel"),
        on_retry=lambda task_id: actions.handle_task_operation(task_id, "retry"),
        on_resume=lambda task_id: actions.handle_task_operation(task_id, "resume"),
    )
