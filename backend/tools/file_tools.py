"""Tools for inspecting supported files in the project data directory."""

from typing import Any

from backend import database
from backend.runtime.safety_policy import classify_file_record
from backend.services.file_locator import FileLocatorError, resolve_by_file_name
from backend.tools import excel_utils
from backend.tools.excel_utils import failure, resolve_data_file, success


SUPPORTED_SUFFIXES = {".xlsx", ".docx", ".pdf", ".jpg", ".jpeg", ".png"}
FILE_TYPES = {
    ".xlsx": "excel",
    ".docx": "word",
    ".pdf": "pdf",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
}


def list_files() -> dict[str, Any]:
    """Return all supported files currently present in data/."""
    try:
        files = []
        data_dir = excel_utils.DATA_DIR
        if data_dir.is_dir():
            paths = [
                path
                for path in data_dir.iterdir()
                if path.is_file() and not path.name.startswith(".")
            ]
            upload_dir = data_dir / "uploads"
            if upload_dir.is_dir():
                paths.extend(
                    path
                    for path in upload_dir.iterdir()
                    if path.is_file() and not path.name.startswith(".")
                )
            for path in sorted(paths, key=lambda item: (item.name, str(item.parent))):
                if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                    relative_path = path.relative_to(data_dir).as_posix()
                    record = database.get_file_record(path.name)
                    item = {
                        "file_name": path.name,
                        "file_type": FILE_TYPES[path.suffix.lower()],
                        "path": f"data/{relative_path}",
                        "size": path.stat().st_size,
                        "exists": True,
                    }
                    item.update(_lifecycle_fields(record, relative_path))
                    files.append(item)
    except OSError as exc:
        return failure("FILE_LIST_ERROR", f"读取 data/ 文件列表失败：{exc}")

    return success(files, f"找到 {len(files)} 个受支持文件")


def get_file_info(file_name: str) -> dict[str, Any]:
    """Return metadata for one supported data file."""
    record = database.get_file_record(file_name.strip()) if isinstance(file_name, str) else None
    if record is not None:
        try:
            path = resolve_by_file_name(file_name)
            exists = True
        except FileLocatorError:
            path = None
            exists = False
        data = {
            "file_name": record["file_name"],
            "file_type": record["file_type"],
            "path": record["file_path"],
            "size": path.stat().st_size if path is not None else 0,
            "exists": exists,
        }
        data.update(_lifecycle_fields(record, record["file_path"]))
        if not exists:
            return failure("FILE_NOT_FOUND", f"文件不存在：{record['file_path']}", data)
        return success(data, "文件信息读取成功")

    try:
        path = resolve_data_file(file_name, SUPPORTED_SUFFIXES)
    except ValueError as exc:
        return failure("INVALID_FILE_NAME", str(exc))

    exists = path.is_file()
    data = {
        "file_name": path.name,
        "file_type": FILE_TYPES[path.suffix.lower()],
        "path": f"data/{path.name}",
        "size": path.stat().st_size if exists else 0,
        "exists": exists,
    }
    if not exists:
        return failure("FILE_NOT_FOUND", f"文件不存在：data/{path.name}", data)
    return success(data, "文件信息读取成功")


def _lifecycle_fields(
    record: dict[str, Any] | None,
    relative_path: str,
) -> dict[str, Any]:
    is_upload = str(relative_path).startswith(("uploads/", "data/uploads/"))
    if record is None:
        return {
            "file_id": None,
            "source_type": "upload" if is_upload else "system",
            "lifecycle_status": "uploaded" if is_upload else "ready",
            "parse_status": "pending" if is_upload else "not_required",
            "queryable": not is_upload,
            "index_status": "not_required",
            "trust_level": "unknown",
        }
    return {
        "file_id": record["file_id"],
        "source_type": record["source_type"],
        "lifecycle_status": record["lifecycle_status"],
        "parse_status": record["parse_status"],
        "queryable": bool(record["queryable"]),
        "index_status": record["index_status"],
        "trust_level": classify_file_record(record).value,
    }
