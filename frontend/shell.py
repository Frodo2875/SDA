"""Shared navigation and visual frame; no backend or account mutations."""

from pathlib import Path

import streamlit as st
from frontend.styles import render_styles

LOGO_PATH = Path(__file__).resolve().parents[1] / "Logo" / "Student_Document_Agent.png"

MENU = {
    "dashboard": "首页", "chat": "新建聊天", "knowledge": "知识库",
    "documents": "我的文档", "tasks": "研究任务", "reports": "报告中心",
    "usage": "使用统计", "settings": "系统设置",
}
MENU_ICONS = dict(zip(MENU, ["home", "chat_bubble", "folder_open", "description",
                             "manage_search", "article", "bar_chart", "settings"]))


def navigate(page: str) -> None:
    st.session_state.active_page = page if page in MENU else "dashboard"


def render_shell() -> None:
    if st.session_state.active_page not in MENU:
        navigate("dashboard")
    render_styles()
    st.logo(str(LOGO_PATH), size="large", icon_image=str(LOGO_PATH))
    with st.sidebar:
        st.markdown("### 学生材料助手")
        st.divider()
        with st.container(key="sda-navigation"):
            for key, label in MENU.items():
                st.button(label, key=f"nav-{key}", use_container_width=True,
                          icon=f":material/{MENU_ICONS[key]}:",
                          type="primary" if st.session_state.active_page == key else "secondary",
                          on_click=navigate, args=(key,))
        st.divider()
        st.caption("Student Document Agent")
    with st.container(key="sda-header"):
        brand, workspace, user = st.columns([3, 2, 1])
        brand.markdown('<div class="platform-brand">Student Document Agent</div>', unsafe_allow_html=True)
        workspace.caption(f"工作空间 · {st.session_state.workspace_name}")
        user.caption("用户 · 本地用户")
