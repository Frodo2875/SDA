"""HITL Diff, confirmation and version result card."""

from collections.abc import Callable
from typing import Any

import streamlit as st


DecisionHandler = Callable[[str], None]


def render_confirmation(
    action: dict[str, Any], *, key_prefix: str, on_decision: DecisionHandler
) -> None:
    status = action.get("status", "pending")
    st.markdown("#### 操作确认")
    left, right = st.columns(2)
    left.markdown(f"**目标学生/对象**  \n{action.get('student_name') or action.get('student_id') or '文件'}")
    right.markdown(f"**目标文件**  \n{action.get('target_file', '—')}")
    diff = action.get("diff_preview") or {}
    if diff:
        st.caption(
            f"操作：{diff.get('operation_type', '—')} · 位置：{diff.get('location', '—')} · "
            f"影响范围：{diff.get('impact_scope', '—')}"
        )
        before_column, after_column = st.columns(2)
        with before_column:
            st.markdown("**修改前**")
            st.code(str(diff.get("before") or "（空）"), language=None)
        with after_column:
            st.markdown("**修改后**")
            st.code(str(diff.get("after") or "（空）"), language=None)
    elif action.get("action_type") == "delete_file":
        st.warning(f"确认删除文件：{action.get('target_file', '—')}。取消不会修改文件或索引。")
    elif action.get("content"):
        with st.container(border=True):
            st.markdown(action["content"])
    if action.get("target_version_id"):
        st.caption(f"目标版本：{action['target_version_id']}")
    st.caption(f"状态：{status}")
    if status == "pending":
        confirm, cancel = st.columns(2)
        if confirm.button("确认", type="primary", use_container_width=True, key=f"{key_prefix}-confirm"):
            on_decision("confirm")
        if cancel.button("取消", use_container_width=True, key=f"{key_prefix}-cancel"):
            on_decision("cancel")
