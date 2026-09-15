"""Shared navigation and visual frame; no backend or account mutations."""

import streamlit as st

MENU = {
    "dashboard": "首页", "chat": "新建聊天", "knowledge": "知识库",
    "documents": "我的文档", "tasks": "研究任务", "reports": "报告中心",
    "usage": "使用统计", "settings": "系统设置",
}


def navigate(page: str) -> None:
    st.session_state.active_page = page if page in MENU else "dashboard"


def render_shell() -> None:
    if st.session_state.active_page not in MENU:
        navigate("dashboard")
    st.markdown("""
    <style>
    :root {--platform-accent: #315ee7; --platform-muted: #64748b;}
    .stApp {font-family: Inter, "Noto Sans SC", "Microsoft YaHei", sans-serif;}
    .block-container {max-width: 1480px; padding-top: 2rem; padding-bottom: 4rem;}
    [data-testid="stSidebar"] {background: #f7f9fc; border-right: 1px solid #e2e8f0;}
    [data-testid="stSidebar"] [data-testid="stButton"] button {text-align: left; border-radius: 8px;}
    [data-testid="stMetric"] {background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 1rem; color: #17213b;}
    [data-testid="stChatMessage"] {border-radius: 10px;}
    button[kind="primary"] {background-color: var(--platform-accent); border-color: var(--platform-accent); color: white;}
    .platform-brand {font-size: 1.2rem; font-weight: 650; letter-spacing: -.02em;}
    </style>
    """, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown("### SDA Workspace")
        st.caption("研究与文档工作空间")
        st.divider()
        for key, label in MENU.items():
            st.button(label, key=f"nav-{key}", use_container_width=True,
                      type="primary" if st.session_state.active_page == key else "secondary",
                      on_click=navigate, args=(key,))
        st.divider()
        st.caption("Student Document Agent · V7")
    brand, workspace, user = st.columns([3, 2, 1])
    brand.markdown('<div class="platform-brand">📚 Student Document Agent</div>', unsafe_allow_html=True)
    workspace.caption(f"Workspace · {st.session_state.workspace_name}")
    user.caption("用户 · 本地用户")
    st.divider()
