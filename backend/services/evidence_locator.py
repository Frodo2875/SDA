"""Resolve persisted Evidence 2.0 IDs into bounded, read-only previews."""

import re
import time
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple

from backend import database
from backend.services.file_locator import FileLocatorError, resolve_by_file_id
from backend.services.trace_service import record_trace
from backend.tools.excel_utils import failure, success


EVIDENCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def locate_evidence(
    evidence_id: str, *, session_id: str | None = None
) -> dict[str, Any]:
    """Return a trusted locator and preview for one actual answer Evidence."""
    clean_id = str(evidence_id or "").strip()
    trace_session = str(session_id or "evidence-preview")[:128]
    started = time.perf_counter()
    if not EVIDENCE_ID_PATTERN.fullmatch(clean_id):
        return _location_failure(
            clean_id, trace_session, "INVALID_EVIDENCE_ID", "Evidence 标识无效"
        )
    evidence = database.get_evidence_location(clean_id)
    if evidence is None:
        return _location_failure(
            clean_id,
            trace_session,
            "EVIDENCE_LOCATION_NOT_FOUND",
            "来源存在，但当前无法打开对应预览位置",
        )
    record = database.get_file_record_by_id(str(evidence.get("file_id") or ""))
    if record is None:
        return _location_failure(
            clean_id,
            trace_session,
            "EVIDENCE_SOURCE_UNAVAILABLE",
            "来源存在，但当前无法打开对应预览位置",
        )
    try:
        path = resolve_by_file_id(record["file_id"])
        if record["file_type"] == "excel":
            location = _locate_excel(evidence, record, path)
        elif record["file_type"] in {"pdf", "word"}:
            location = _locate_document(evidence, record)
        else:
            raise ValueError("不支持的 Evidence 文件类型")
    except (FileLocatorError, KeyError, TypeError, ValueError, OSError):
        return _location_failure(
            clean_id,
            trace_session,
            "EVIDENCE_PREVIEW_UNAVAILABLE",
            "来源存在，但当前无法打开对应预览位置",
        )
    result = success(location, "Evidence 原文定位成功")
    record_trace(
        session_id=trace_session,
        event_type="evidence_location",
        tool_name="locate_evidence",
        arguments={"evidence_id": clean_id},
        result=result,
        duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
        result_status="success",
        metrics={
            "evidence_id": clean_id,
            "location_type": location.get("location_type"),
            "page_no": location.get("page_no"),
            "has_bbox": location.get("bbox") is not None,
        },
    )
    return result


def _locate_document(
    evidence: dict[str, Any], record: dict[str, Any]
) -> dict[str, Any]:
    chunks = database.get_document_chunks(record["file_id"])
    chunk = next(
        (
            item
            for item in chunks
            if item.get("chunk_id") == evidence.get("chunk_id")
            or (
                evidence.get("block_id")
                and (item.get("metadata") or {}).get("block_id")
                == evidence.get("block_id")
            )
        ),
        None,
    )
    if chunk is None:
        raise ValueError("Evidence 对应的活动索引已不可用")
    metadata = chunk.get("metadata") or {}
    block = metadata.get("block") or {}
    page_no = evidence.get("page_no") or chunk.get("page_no")
    bbox = evidence.get("bbox") or metadata.get("bbox") or block.get("bbox")
    confidence = (
        evidence.get("confidence")
        if evidence.get("confidence") is not None
        else metadata.get("confidence", block.get("confidence"))
    )
    base = {
        "evidence_id": evidence["evidence_id"],
        "source_type": evidence["source_type"],
        "file_id": record["file_id"],
        "file_name": record["file_name"],
        "block_id": evidence.get("block_id") or metadata.get("block_id"),
        "chunk_id": chunk["chunk_id"],
        "text": chunk.get("chunk_text") or block.get("content") or "",
        "bbox": bbox,
        "confidence": confidence,
        "table_id": evidence.get("table") or metadata.get("table_id"),
        "cell_id": evidence.get("cell") or metadata.get("cell_id"),
        "row_index": metadata.get("row_index"),
        "column_index": metadata.get("column_index"),
    }
    if record["file_type"] == "pdf":
        if page_no is None:
            raise ValueError("PDF Evidence 缺少页码")
        return {
            **base,
            "location_type": "pdf",
            "page_no": int(page_no),
            "highlight": {"bbox": bbox} if bbox is not None else None,
        }
    return {
        **base,
        "location_type": "word",
        "paragraph_no": metadata.get("paragraph_no")
        or metadata.get("paragraph_start"),
        "word_region": metadata.get("word_region"),
    }


def _locate_excel(
    evidence: dict[str, Any], record: dict[str, Any], path: Any
) -> dict[str, Any]:
    sheet_name = str(evidence.get("sheet") or evidence.get("table") or "")
    cell_id = str(evidence.get("cell") or "")
    if not sheet_name or not cell_id:
        raise ValueError("Excel Evidence 缺少 Sheet 或 Cell")
    row_index, column_index = coordinate_to_tuple(cell_id)
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet_name not in workbook.sheetnames:
            raise ValueError("Evidence Sheet 已不存在")
        worksheet = workbook[sheet_name]
        if row_index > worksheet.max_row or column_index > worksheet.max_column:
            raise ValueError("Evidence Cell 已不存在")
        schema_rows = database.get_table_schema_records(record["file_id"], sheet_name)
        header_row = int(schema_rows[0]["header_row"]) if schema_rows else 1
        headers = [
            str(worksheet.cell(header_row, column).value or f"Column {column}")
            for column in range(1, worksheet.max_column + 1)
        ]
        row_values = [
            worksheet.cell(row_index, column).value
            for column in range(1, worksheet.max_column + 1)
        ]
        target_value = worksheet.cell(row_index, column_index).value
    finally:
        workbook.close()
    return {
        "evidence_id": evidence["evidence_id"],
        "location_type": "excel",
        "source_type": evidence["source_type"],
        "file_id": record["file_id"],
        "file_name": record["file_name"],
        "sheet": sheet_name,
        "table_id": evidence.get("table") or sheet_name,
        "cell_id": cell_id,
        "row_index": row_index,
        "column_index": column_index,
        "record_identifier": evidence.get("record_key"),
        "field": evidence.get("field"),
        "bbox": evidence.get("bbox"),
        "confidence": evidence.get("confidence"),
        "headers": headers,
        "row_values": row_values,
        "target_value": target_value,
    }


def _location_failure(
    evidence_id: str, session_id: str, error_code: str, message: str
) -> dict[str, Any]:
    result = failure(error_code, message)
    record_trace(
        session_id=session_id,
        event_type="evidence_location",
        tool_name="locate_evidence",
        arguments={"evidence_id": evidence_id},
        result=result,
        result_status="failed",
        error_code=error_code,
    )
    return result
