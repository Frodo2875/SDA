"""V7 product entry point: common shell around independently rendered pages."""

import streamlit as st

from frontend import controller
from frontend.shell import LOGO_PATH, render_shell
from frontend.pages import dashboard, chat, documents, tasks, settings, placeholder, knowledge, reports
from frontend.pages import usage


def render_page() -> None:
    controller.initialize_state()
    if not st.session_state.files_loaded:
        controller.refresh_files()
    render_shell()
    pages = {
        "dashboard": dashboard.render,
        "chat": chat.render,
        "documents": documents.render,
        "knowledge": knowledge.render,
        "tasks": tasks.render,
        "reports": reports.render,
        "usage": usage.render,
        "settings": settings.render,
    }
    pages.get(st.session_state.active_page, placeholder.render)()


st.set_page_config(page_title="Student Document Agent", page_icon=str(LOGO_PATH), layout="wide", initial_sidebar_state="expanded")
# Explicit navigation disables automatic pages/ discovery. The sidebar changes
# one session route while all sections share the existing HTTP and widget state.
st.navigation([st.Page(render_page, title="Workspace", default=True)], position="hidden").run()
