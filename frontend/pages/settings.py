"""Read-only workspace information; no credentials or server configuration."""

import streamlit as st
from frontend.system_dashboard import render_status


def render() -> None:
    st.title("系统设置")
    with st.container(border=True):
        st.markdown("**当前工作空间**")
        st.write(st.session_state.workspace_name)
        st.caption("本地工作空间 · 当前用户：本地用户")
    st.info("只读系统信息；配置由后台管理，本页不提供密钥、权限或安全策略编辑。")
    render_status()
