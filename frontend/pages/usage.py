"""Scoped usage counts from the shared read-only status snapshot."""

import streamlit as st
from frontend.system_dashboard import render_status


def render() -> None:
    st.title("使用统计")
    st.info("点击刷新查看最新统计。")
    render_status()
