"""Knowledge-base files, lifecycle metadata, upload and version controls."""

from collections.abc import Callable
from typing import Any

import streamlit as st

from frontend import api_client


ActionHandler = Callable[[dict[str, Any]], None]
FILE_TYPES = {"excel": "Excel", "word": "Word", "pdf": "PDF"}
LIFECYCLE_LABELS = {
    "uploaded": "已上传",
    "detecting": "检测中",
    "parsing": "解析中",
    "ocr_processing": "OCR处理中",
    "layout_processing": "Layout处理中",
    "indexing": "索引中",
    "reprocessing": "重新解析中",
    "reindexing": "重新索引中",
    "queryable": "可查询",
    "processing": "处理中",
    "ready": "可用",
    "failed": "失败",
    "deleted": "已删除",
    "cleanup_failed": "清理失败",
}
LIFECYCLE_FILTERS = {
    "全部状态": None,
    "UPLOADED": "uploaded",
    "DETECTING": "detecting",
    "PARSING": "parsing",
    "OCR_PROCESSING": "ocr_processing",
    "LAYOUT_PROCESSING": "layout_processing",
    "INDEXING": "indexing",
    "QUERYABLE": "queryable",
    "READY（兼容）": "ready",
    "FAILED": "failed",
}


def format_file_size(value: Any) -> str:
    size = max(0, int(value or 0))
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def file_card_fields(item: dict[str, Any]) -> dict[str, str]:
    lifecycle = str(
        item.get("canonical_status") or item.get("lifecycle_status") or "—"
    ).casefold()
    return {
        "file_name": str(item.get("file_name") or "未命名文件"),
        "file_type": FILE_TYPES.get(item.get("file_type"), str(item.get("file_type") or "未知")),
        "size": format_file_size(item.get("size")),
        "created_time": str(item.get("created_time") or item.get("uploaded_at") or "—"),
        "lifecycle_status": LIFECYCLE_LABELS.get(lifecycle, lifecycle),
        "index_status": str(item.get("index_status") or "—"),
        "queryable": "是" if item.get("queryable") else "否",
    }


def file_operation_capabilities(item: dict[str, Any]) -> dict[str, bool]:
    """Keep UI affordances aligned with server-side file operation rules."""
    available = bool(item.get("file_id")) and str(
        item.get("lifecycle_status") or ""
    ).casefold() != "deleted"
    document = item.get("file_type") in {"word", "pdf"}
    return {
        "details": available,
        "preview": available,
        "reprocess": available and document,
        "reindex": available and document,
        "delete": available
        and item.get("source_type") == "upload"
        and bool(item.get("deletable")),
    }


def filter_files_by_lifecycle(
    files: list[dict[str, Any]], selected_label: str
) -> list[dict[str, Any]]:
    expected = LIFECYCLE_FILTERS.get(selected_label)
    if expected is None:
        return files
    return [
        item
        for item in files
        if str(item.get("lifecycle_status") or "").casefold() == expected
    ]


def file_detail_sections(
    detail: dict[str, Any], versions: list[dict[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    """Map API fields to type-specific V3 detail rows without inventing values."""
    file_type = detail.get("file_type")
    if file_type == "pdf":
        summary = detail.get("document_summary") or {}
        return {"PDF": {
            "页数": detail.get("page_count", summary.get("page_count", "暂无数据")),
            "OCR状态": detail.get("ocr_status", "暂无数据"),
            "Layout状态": detail.get("layout_status", "暂无数据"),
            "Block数量": detail.get("block_count", summary.get("block_count", "暂无数据")),
            "Chunk数量": detail.get("chunk_count", summary.get("chunk_count", "暂无数据")),
        }}
    if file_type == "excel":
        schema = detail.get("schema_summary") or {}
        sheets = schema.get("sheets") or []
        return {"Excel": {
            "Sheet": ", ".join(str(item.get("sheet_name")) for item in sheets) or "暂无数据",
            "Schema": "已识别" if schema else "暂无数据",
            "字段": schema.get("field_count", "暂无数据"),
            "质量检查结果": detail.get("quality_status", "暂无检查结果"),
        }}
    if file_type == "word":
        version_rows = versions or []
        latest = version_rows[-1] if version_rows else None
        return {"Word": {
            "版本": latest.get("version_number") if latest else "暂无版本",
            "Diff": detail.get("diff_status", "操作确认时展示"),
            "写入状态": detail.get("write_status", "无待执行写入"),
        }}
    return {}


def render_file_panel(
    *,
    files: list[dict[str, Any]],
    session_id: str,
    uploader_version: int,
    upload_notice: list[dict[str, Any]] | None,
    files_error: str | None,
    on_upload: Callable[[list[Any]], None],
    on_refresh: Callable[[], None],
    on_version_action: ActionHandler,
) -> None:
    st.subheader("Document Workspace")
    st.caption("文件、生命周期、解析与索引状态")
    uploaded = st.file_uploader(
        "拖拽或选择多个材料",
        type=["xlsx", "docx", "pdf"],
        accept_multiple_files=True,
        help="支持 Excel、Word、文本 PDF 和扫描 PDF。文件将逐个上传并显示结果。",
        key=f"material-uploader-{uploader_version}",
    )
    if st.button(
        "上传所选文件",
        type="primary",
        use_container_width=True,
        disabled=not uploaded,
    ):
        on_upload(list(uploaded or []))
    for outcome in upload_notice or []:
        renderer = st.success if outcome.get("status") == "success" else st.error
        renderer(f"{outcome.get('file_name', '文件')}：{outcome.get('message', '')}")
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
        list(LIFECYCLE_FILTERS),
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
            _render_file(item, session_id, on_refresh, on_version_action)


def _render_file(
    item: dict[str, Any],
    session_id: str,
    on_refresh: Callable[[], None],
    on_version_action: ActionHandler,
) -> None:
    with st.container(border=True):
        fields = file_card_fields(item)
        capabilities = file_operation_capabilities(item)
        st.markdown(f"**{fields['file_name']}**")
        st.caption(f"{fields['file_type']} · {fields['size']}")
        st.markdown(f"`{fields['lifecycle_status']}` · `索引 {fields['index_status']}`")
        st.caption(
            f"可查询：{fields['queryable']} · "
            f"来源：{'系统' if item.get('source_type') == 'system' else '上传'}"
        )
        st.caption(f"上传/登记时间：{fields['created_time']}")
        schema = item.get("schema_summary") or {}
        if item.get("file_type") == "excel":
            st.caption(
                f"Sheet：{schema.get('sheet_count', '—')} · 字段：{schema.get('field_count', '—')} · "
                f"数据行：{schema.get('row_count', '—')}"
            )
        if item.get("file_type") in {"word", "pdf"}:
            st.caption(f"文档索引状态：{item.get('index_status', '—')}")
        error_summary = item.get("error_summary") or {}
        if error_summary:
            st.error(
                f"最近操作失败（{error_summary.get('failure_stage') or 'unknown'}）："
                f"{error_summary.get('message') or error_summary.get('error_code') or '未知错误'}"
            )
        notice = st.session_state.pop(
            f"workspace-operation-notice-{item.get('file_id')}", None
        )
        if notice:
            (st.success if notice.get("ok") else st.error)(notice.get("message", ""))

        view_columns = st.columns(2)
        if capabilities["details"] and view_columns[0].button(
            "查看详情", key=f"details-{item['file_id']}", use_container_width=True
        ):
            st.session_state.workspace_selected_file_id = item["file_id"]
            st.rerun()
        if capabilities["preview"] and view_columns[1].button(
            "快速预览", key=f"preview-{item['file_id']}", use_container_width=True
        ):
            st.session_state.workspace_preview_file_id = item["file_id"]
            st.rerun()
        if st.session_state.get("workspace_selected_file_id") == item.get("file_id"):
            _render_file_details(item["file_id"])
        if st.session_state.get("workspace_preview_file_id") == item.get("file_id"):
            _render_file_preview(item["file_id"])

        if capabilities["reprocess"] or capabilities["reindex"]:
            processing_columns = st.columns(2)
            if processing_columns[0].button(
                "重新解析", key=f"reprocess-{item['file_id']}", use_container_width=True
            ):
                _run_document_operation(
                    item,
                    label="重新解析",
                    status="REPROCESSING",
                    operation=api_client.reprocess_file,
                    on_refresh=on_refresh,
                )
            if processing_columns[1].button(
                "重新索引", key=f"reindex-{item['file_id']}", use_container_width=True
            ):
                _run_document_operation(
                    item,
                    label="重新索引",
                    status="REINDEXING",
                    operation=api_client.reindex_file,
                    on_refresh=on_refresh,
                )
        elif item.get("file_type") == "excel":
            st.caption("Excel 使用结构化 Schema，不提供文档 Block 重解析/重索引。")

        if capabilities["delete"] and st.button(
            "删除文件",
            key=f"delete-{item['file_id']}",
            use_container_width=True,
        ):
            try:
                on_version_action(api_client.prepare_delete(item["file_id"], session_id))
            except RuntimeError as exc:
                st.error(str(exc))
        elif item.get("source_type") == "system":
            st.caption("系统固定文件受保护，不提供删除入口。")
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
            f"**状态**：生命周期 `{detail.get('canonical_status') or detail.get('lifecycle_status', '—')}` · "
            f"解析 `{detail.get('parse_status', '—')}` · "
            f"索引 `{detail.get('index_status', '—')}`"
        )
        error_summary = detail.get("error_summary") or {}
        if error_summary:
            st.error(
                f"错误阶段：{error_summary.get('failure_stage') or 'unknown'} · "
                f"{error_summary.get('message') or error_summary.get('error_code') or '未知错误'}"
            )
        versions = []
        if detail.get("file_type") == "word":
            try:
                versions = api_client.list_versions(file_id)
            except RuntimeError:
                versions = []
        for title, rows in file_detail_sections(detail, versions).items():
            st.markdown(f"**{title} 详情**")
            for label, value in rows.items():
                st.caption(f"{label}：{value}")


def _render_file_preview(file_id: str) -> None:
    with st.expander("快速预览", expanded=True):
        try:
            preview = api_client.get_file_preview(file_id)
        except RuntimeError as exc:
            st.warning(str(exc))
            return
        if preview.get("preview_type") == "table":
            for sheet in preview.get("sheets") or []:
                st.markdown(f"**Sheet：{sheet.get('sheet_name', '—')}**")
                st.dataframe(sheet.get("rows") or [], use_container_width=True)
        else:
            for item in preview.get("items") or []:
                st.caption(item.get("location") or "内容")
                st.code(str(item.get("text") or "（空）"), language=None)
        if preview.get("truncated"):
            st.caption("预览已按安全上限截断。")


def _run_document_operation(
    item: dict[str, Any],
    *,
    label: str,
    status: str,
    operation: Callable[[str], dict[str, Any]],
    on_refresh: Callable[[], None],
) -> None:
    file_id = item["file_id"]
    status_box = st.status(
        f"{label}中：{item.get('file_name', file_id)} · {status}", expanded=True
    )
    try:
        result = operation(file_id)
        message = result.get("message") or f"{label}完成"
        st.session_state[f"workspace-operation-notice-{file_id}"] = {
            "ok": True,
            "message": message,
        }
        status_box.update(label=f"{label}完成 · QUERYABLE", state="complete")
    except RuntimeError as exc:
        st.session_state[f"workspace-operation-notice-{file_id}"] = {
            "ok": False,
            "message": str(exc),
        }
        status_box.update(label=f"{label}失败", state="error")
    on_refresh()


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
