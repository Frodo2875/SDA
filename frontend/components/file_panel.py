"""Knowledge-base files, lifecycle metadata, upload and version controls."""

from collections.abc import Callable
from typing import Any

import streamlit as st

from frontend import api_client


ActionHandler = Callable[[dict[str, Any]], None]
FILE_TYPES = {"excel": "Excel", "word": "Word", "pdf": "PDF"}
LIFECYCLE_LABELS = {
    "uploaded": "已上传",
    "processing": "处理中",
    "ready": "可用",
    "failed": "失败",
    "deleted": "已删除",
    "cleanup_failed": "清理失败",
}


def render_file_panel(
    *,
    files: list[dict[str, Any]],
    session_id: str,
    uploader_version: int,
    upload_notice: dict[str, Any] | None,
    files_error: str | None,
    on_upload: Callable[[Any], None],
    on_refresh: Callable[[], None],
    on_version_action: ActionHandler,
) -> None:
    st.title("学生材料智能文档助手")
    st.caption("Student Document Agent V2")
    st.divider()
    st.subheader("文档工作区")
    uploaded = st.file_uploader(
        "上传材料", type=["xlsx", "docx", "pdf"], accept_multiple_files=False,
        help="支持 Excel、Word 和普通文本 PDF；扫描 PDF / OCR 暂不支持。",
        key=f"material-uploader-{uploader_version}",
    )
    if st.button("上传文件", type="primary", use_container_width=True, disabled=uploaded is None):
        on_upload(uploaded)
    if upload_notice:
        (st.success if upload_notice.get("ok") else st.error)(upload_notice.get("message", ""))
    if st.button("刷新文件列表", use_container_width=True, icon="🔄"):
        on_refresh()
    st.text_input(
        "搜索文件名",
        key="workspace_search",
        placeholder="输入文件名关键词",
    )
    filter_columns = st.columns(2)
    filter_columns[0].selectbox(
        "文件类型",
        ["全部类型", "Excel", "Word", "PDF"],
        key="workspace_file_type",
    )
    filter_columns[1].selectbox(
        "生命周期",
        ["全部状态", "已上传", "处理中", "可用", "失败", "已删除", "清理失败"],
        key="workspace_lifecycle",
    )
    st.selectbox(
        "排序",
        [
            "登记时间（新到旧）",
            "登记时间（旧到新）",
            "文件大小（大到小）",
            "文件大小（小到大）",
        ],
        key="workspace_sort",
    )
    if st.button("应用搜索 / 筛选 / 排序", use_container_width=True):
        on_refresh()
    if files_error:
        st.warning(files_error)
        return
    if not files:
        st.info("当前没有可用材料文件。")
        return
    for source_type, title in (("system", "系统文件"), ("upload", "上传文件")):
        group = [item for item in files if item.get("source_type") == source_type]
        if not group:
            continue
        st.markdown(f"#### {title}")
        for item in group:
            _render_file(item, session_id, on_version_action)


def _render_file(
    item: dict[str, Any], session_id: str, on_version_action: ActionHandler
) -> None:
    with st.container(border=True):
        st.markdown(f"**{item.get('file_name', '未命名文件')}**")
        file_type = FILE_TYPES.get(item.get("file_type"), item.get("file_type", "未知"))
        st.caption(f"{file_type} · {int(item.get('size') or 0):,} bytes")
        lifecycle = item.get("lifecycle_status", "—")
        lifecycle_label = LIFECYCLE_LABELS.get(lifecycle, lifecycle)
        st.markdown(
            f"`{lifecycle_label}` · `解析 {item.get('parse_status', '—')}` · "
            f"`索引 {item.get('index_status', '—')}`"
        )
        st.caption(
            f"记录状态：{item.get('status', '—')} · "
            f"可查询：{'是' if item.get('queryable') else '否'} · "
            f"来源：{'系统' if item.get('source_type') == 'system' else '上传'}"
        )
        if item.get("created_time") or item.get("uploaded_at"):
            st.caption(f"上传/登记时间：{item.get('created_time') or item['uploaded_at']}")
        schema = item.get("schema_summary") or {}
        if item.get("file_type") == "excel":
            st.caption(
                f"Sheet：{schema.get('sheet_count', '—')} · 字段：{schema.get('field_count', '—')} · "
                f"数据行：{schema.get('row_count', '—')}"
            )
        if item.get("file_type") in {"word", "pdf"}:
            st.caption(f"文档索引状态：{item.get('index_status', '—')}")
        if item.get("file_id") and st.button(
            "查看文件详情",
            key=f"details-{item['file_id']}",
            use_container_width=True,
        ):
            st.session_state.workspace_selected_file_id = item["file_id"]
            st.rerun()
        if st.session_state.get("workspace_selected_file_id") == item.get("file_id"):
            _render_file_details(item["file_id"])
        if item.get("file_type") == "word" and item.get("writable") and item.get("file_id"):
            _render_versions(item, session_id, on_version_action)


def _render_file_details(file_id: str) -> None:
    with st.expander("文件详情", expanded=True):
        try:
            detail = api_client.get_file_detail(file_id)
        except RuntimeError as exc:
            st.warning(str(exc))
            return
        st.markdown(f"**基本信息**：{detail.get('file_name', '—')}")
        st.caption(
            f"类型：{FILE_TYPES.get(detail.get('file_type'), detail.get('file_type', '—'))} · "
            f"大小：{int(detail.get('size') or 0):,} bytes · "
            f"登记时间：{detail.get('created_time') or '—'}"
        )
        st.markdown(
            f"**状态**：生命周期 `{detail.get('lifecycle_status', '—')}` · "
            f"解析 `{detail.get('parse_status', '—')}` · "
            f"索引 `{detail.get('index_status', '—')}`"
        )


def _render_versions(
    item: dict[str, Any], session_id: str, on_version_action: ActionHandler
) -> None:
    with st.expander("历史版本 / Undo / Rollback"):
        try:
            versions = api_client.list_versions(item["file_id"])
        except RuntimeError as exc:
            st.warning(str(exc))
            return
        if st.button("Undo 最近修改", key=f"undo-{item['file_id']}", use_container_width=True):
            try:
                on_version_action(api_client.prepare_undo(item["file_id"], session_id))
            except RuntimeError as exc:
                st.error(str(exc))
        if not versions:
            st.caption("暂无版本记录。")
            return
        for version in reversed(versions):
            columns = st.columns([3, 1])
            columns[0].caption(
                f"v{version.get('version_number')} · {version.get('change_type')} · "
                f"{version.get('created_at')}"
            )
            if columns[1].button("Rollback", key=f"rollback-{version['version_id']}"):
                try:
                    on_version_action(
                        api_client.prepare_rollback(
                            item["file_id"], version["version_id"], session_id
                        )
                    )
                except RuntimeError as exc:
                    st.error(str(exc))
