"""Scoped usage counts from the shared read-only status snapshot."""

import streamlit as st
from frontend.system_dashboard import render_status


def render() -> None:
    st.title("使用统计")
    st.info("统计需点击刷新获取；不可用数据以 — 表示。")
    render_status()
