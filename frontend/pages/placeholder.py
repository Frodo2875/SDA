"""Navigation placeholders without knowledge, report or usage business calls."""

import streamlit as st
from frontend.shell import MENU


def render() -> None:
    name = MENU[st.session_state.active_page]
    st.title(name)
    st.info(f"{name}尚未开放。")
    st.caption("已有文档和任务可从左侧导航继续访问。")
