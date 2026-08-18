"""Read-only file presentation metadata composed from existing repositories."""

from typing import Any

from backend import database
from backend.tools.excel_utils import failure, success
from backend.tools.file_tools import list_files


def list_file_views() -> dict[str, Any]:
    result = list_files()
    if not result["ok"]:
        return result
    return success([_enrich(item) for item in result["data"]], result["message"])


def get_file_view(file_id: str) -> dict[str, Any]:
    clean = str(file_id).strip()
    record = database.get_file_record_by_id(clean)
    if record is None:
        return failure("FILE_NOT_FOUND", "未找到指定文件")
    listed = list_files()
    item = next(
        (entry for entry in (listed.get("data") or []) if entry.get("file_id") == clean),
        None,
    )
    if item is None:
        return failure("FILE_NOT_FOUND", "文件记录存在，但物理文件不可用")
    return success(_enrich(item), "文件展示信息读取成功")


def _enrich(item: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(item)
    record = (
        database.get_file_record_by_id(item["file_id"])
        if item.get("file_id")
        else database.get_file_record(item["file_name"])
    )
    enriched["uploaded_at"] = record.get("created_at") if record else None
    enriched["updated_at"] = record.get("updated_at") if record else None
    enriched["writable"] = bool(record.get("writable")) if record else False
    enriched["deletable"] = bool(record.get("deletable")) if record else False
    enriched["schema_summary"] = None
    if item.get("file_type") == "excel" and item.get("file_id"):
        schemas = database.get_table_schema_records(item["file_id"])
        enriched["schema_summary"] = {
            "sheet_count": len(schemas),
            "field_count": sum(int(schema.get("column_count") or 0) for schema in schemas),
            "row_count": sum(int(schema.get("row_count") or 0) for schema in schemas),
            "sheets": [
                {
                    "sheet_name": schema["sheet_name"],
                    "field_count": schema["column_count"],
                    "row_count": schema["row_count"],
                }
                for schema in schemas
            ],
        } if schemas else None
    return enriched
