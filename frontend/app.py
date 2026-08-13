"""Streamlit frontend for the Student Document Agent FastAPI service."""

import os
from typing import Any
from uuid import uuid4

import httpx
import streamlit as st


BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT = 60.0

TOOL_STATUS_LABELS = {
    "search_student": "正在查询学生身份…",
    "get_student_info": "正在查询学生身份…",
    "get_student_scores": "正在读取学生成绩…",
    "get_student_research": "正在查询科研成果…",
    "compare_students": "正在比较学生信息…",
    "get_top_three_students": "正在统计成绩排名…",
}
FILE_TYPE_LABELS = {"excel": "Excel", "word": "Word"}
COMPARISON_COLUMNS = {
    "student_id": "学号",
    "name": "姓名",
    "average_score": "平均成绩",
    "rank": "专业排名",
    "paper_count": "论文数",
    "patent_count": "专利数",
    "competition_count": "竞赛数",
    "score_status": "成绩状态",
    "research_status": "科研状态",
}


def _initialize_state() -> None:
    defaults = {
        "session_id": uuid4().hex,
        "messages": [],
        "files": [],
        "files_loaded": False,
        "files_error": None,
        "upload_notice": None,
        "uploader_version": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """Call FastAPI and return JSON without exposing transport internals."""
    try:
        response = httpx.request(
            method,
            f"{BACKEND_BASE_URL}{path}",
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        )
    except httpx.RequestError as exc:
        raise RuntimeError("无法连接后端服务，请确认 FastAPI 已在 8000 端口启动。") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("后端返回了无法识别的响应。") from exc
    if response.is_error:
        message = payload.get("message") or payload.get("answer") or "请求处理失败"
        raise RuntimeError(str(message))
    return payload


def _refresh_files() -> None:
    try:
        result = _request("GET", "/api/files")
        if not result.get("ok"):
            raise RuntimeError(result.get("message") or "文件列表读取失败")
        st.session_state.files = result.get("data") or []
        st.session_state.files_error = None
    except RuntimeError as exc:
        st.session_state.files = []
        st.session_state.files_error = str(exc)
    st.session_state.files_loaded = True


def _upload_file(uploaded_file: Any) -> None:
    """Send the selected file to FastAPI; never read project files locally."""
    if uploaded_file is None:
        return
    try:
        with st.spinner("正在校验并上传文件…"):
            result = _request(
                "POST",
                "/api/files/upload",
                files={
                    "file": (
                        uploaded_file.name,
                        uploaded_file.getvalue(),
                        uploaded_file.type or "application/octet-stream",
                    )
                },
            )
        st.session_state.upload_notice = {
            "ok": True,
            "message": result.get("message") or "文件上传成功",
        }
        st.session_state.uploader_version += 1
        _refresh_files()
    except RuntimeError as exc:
        st.session_state.upload_notice = {"ok": False, "message": str(exc)}
    st.rerun()


def _tool_statuses(response: dict[str, Any]) -> list[str]:
    statuses: list[str] = []
    for call in response.get("tool_calls") or []:
        label = TOOL_STATUS_LABELS.get(call.get("name"))
        if label and label not in statuses:
            statuses.append(label)
    if response.get("status") == "confirmation_required":
        statuses.append("正在生成综合评价…")
    return statuses


def _comparison_tables(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract only user-facing comparison rows from Tool results."""
    tables = []
    for call in response.get("tool_calls") or []:
        if call.get("name") != "compare_students":
            continue
        result = call.get("result") or {}
        data = result.get("data") or {}
        students = data.get("students") or []
        if students:
            tables.append(
                [
                    {
                        COMPARISON_COLUMNS[key]: row.get(key)
                        for key in COMPARISON_COLUMNS
                    }
                    for row in students
                ]
            )
    return tables


def _submit_message(message: str) -> None:
    st.session_state.messages.append({"role": "user", "content": message})
    try:
        with st.spinner("Agent 正在处理材料…"):
            response = _request(
                "POST",
                "/api/chat",
                json={"session_id": st.session_state.session_id, "message": message},
            )
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": response.get("answer") or "任务已处理。",
                "statuses": _tool_statuses(response),
                "comparison_tables": _comparison_tables(response),
                "pending_action": response.get("pending_action"),
            }
        )
    except RuntimeError as exc:
        st.session_state.messages.append(
            {"role": "assistant", "content": f"请求失败：{exc}", "error": True}
        )


def _handle_action(message_index: int, decision: str) -> None:
    message = st.session_state.messages[message_index]
    action = message.get("pending_action") or {}
    action_id = action.get("action_id")
    if not action_id or action.get("status") != "pending":
        return

    endpoint = "confirm" if decision == "confirm" else "cancel"
    try:
        result = _request("POST", f"/api/actions/{action_id}/{endpoint}")
        if decision == "confirm":
            updated = (result.get("data") or {}).get("pending_action") or {}
            message["pending_action"] = {**action, **updated, "status": "executed"}
            feedback = "写入已确认，内容已成功追加到目标文件。"
        else:
            updated = result.get("data") or {}
            message["pending_action"] = {**action, **updated, "status": "cancelled"}
            feedback = "已取消本次写入，目标文件没有变化。"
        st.session_state.messages.append({"role": "assistant", "content": feedback})
    except RuntimeError as exc:
        st.session_state.messages.append(
            {"role": "assistant", "content": f"操作失败：{exc}", "error": True}
        )
    st.rerun()


def _render_pending_action(action: dict[str, Any], message_index: int) -> None:
    status = action.get("status", "pending")
    status_labels = {
        "pending": "等待确认",
        "confirmed": "执行中",
        "cancelled": "已取消",
        "executed": "已写入",
        "failed": "写入失败",
    }
    st.markdown("#### 写入确认")
    left, right = st.columns(2)
    left.markdown(
        f"**目标学生**  \n{action.get('student_name', '—')}（{action.get('student_id', '—')}）"
    )
    right.markdown(f"**目标文件**  \n{action.get('target_file', '—')}")
    st.markdown("**待写入内容**")
    with st.container(border=True):
        st.markdown(action.get("content") or "暂无内容")
    st.caption(f"状态：{status_labels.get(status, status)}")

    if status == "pending":
        confirm_column, cancel_column = st.columns(2)
        if confirm_column.button(
            "确认写入",
            type="primary",
            use_container_width=True,
            key=f"confirm-{action['action_id']}",
        ):
            _handle_action(message_index, "confirm")
        if cancel_column.button(
            "取消",
            use_container_width=True,
            key=f"cancel-{action['action_id']}",
        ):
            _handle_action(message_index, "cancel")


def _render_message(message: dict[str, Any], index: int) -> None:
    avatar = "🎓" if message["role"] == "assistant" else "👤"
    with st.chat_message(message["role"], avatar=avatar):
        for status in message.get("statuses") or []:
            st.caption(f"✓ {status}")
        if message.get("error"):
            st.error(message["content"])
        else:
            st.markdown(message["content"])
        for table in message.get("comparison_tables") or []:
            st.dataframe(table, use_container_width=True, hide_index=True)
        if message.get("pending_action"):
            _render_pending_action(message["pending_action"], index)


st.set_page_config(
    page_title="学生材料智能文档助手",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container {max-width: 980px; padding-top: 2rem; padding-bottom: 6rem;}
    [data-testid="stSidebar"] {border-right: 1px solid #e8edf3;}
    [data-testid="stSidebar"] .block-container {padding-top: 1.8rem;}
    div[data-testid="stChatMessage"] {border-radius: 14px; padding: 0.35rem 0.7rem;}
    .app-kicker {color: #55718f; font-size: 0.92rem; margin-bottom: 0.2rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

_initialize_state()
if not st.session_state.files_loaded:
    _refresh_files()

with st.sidebar:
    st.title("学生材料智能文档助手")
    st.caption("Student Document Agent")
    st.divider()
    st.subheader("当前知识库")
    uploaded_file = st.file_uploader(
        "上传材料",
        type=["xlsx", "docx"],
        accept_multiple_files=False,
        help="仅支持 Excel（.xlsx）和 Word（.docx），同名文件不会被覆盖。",
        key=f"material-uploader-{st.session_state.uploader_version}",
    )
    if st.button(
        "上传文件",
        type="primary",
        use_container_width=True,
        disabled=uploaded_file is None,
    ):
        _upload_file(uploaded_file)
    if st.session_state.upload_notice:
        notice = st.session_state.upload_notice
        if notice["ok"]:
            st.success(notice["message"])
        else:
            st.error(notice["message"])
    if st.button("刷新文件列表", use_container_width=True, icon="🔄"):
        _refresh_files()
    if st.session_state.files_error:
        st.warning(st.session_state.files_error)
    elif not st.session_state.files:
        st.info("当前没有可用材料文件。")
    else:
        for file in st.session_state.files:
            with st.container(border=True):
                st.markdown(f"**{file.get('file_name', '未命名文件')}**")
                file_type = FILE_TYPE_LABELS.get(
                    file.get("file_type"), file.get("file_type", "未知")
                )
                status = "可用" if file.get("exists") else "不可用"
                st.caption(f"类型：{file_type} · 状态：{status}")

st.markdown('<p class="app-kicker">学生材料智能文档助手</p>', unsafe_allow_html=True)
st.title("你好，需要查询哪份学生材料？")
st.caption("可以查询学生信息、成绩、科研成果，或比较多名学生的综合情况。")

if not st.session_state.messages:
    with st.chat_message("assistant", avatar="🎓"):
        st.markdown("欢迎使用。你可以尝试输入：`综合分析 S001`。")

for message_index, chat_message in enumerate(st.session_state.messages):
    _render_message(chat_message, message_index)

if prompt := st.chat_input("输入你的问题，例如：比较 S001 和 S002 的综合情况"):
    _submit_message(prompt.strip())
    st.rerun()
