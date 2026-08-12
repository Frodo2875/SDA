"""Tools for inspecting supported files in the project data directory."""

from typing import Any

from backend.tools.excel_utils import DATA_DIR, failure, resolve_data_file, success


SUPPORTED_SUFFIXES = {".xlsx", ".docx"}
FILE_TYPES = {".xlsx": "excel", ".docx": "word"}


def list_files() -> dict[str, Any]:
    """Return all supported files currently present in data/."""
    try:
        files = []
        if DATA_DIR.is_dir():
            for path in sorted(DATA_DIR.iterdir(), key=lambda item: item.name):
                if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                    files.append(
                        {
                            "file_name": path.name,
                            "file_type": FILE_TYPES[path.suffix.lower()],
                            "path": f"data/{path.name}",
                            "size": path.stat().st_size,
                            "exists": True,
                        }
                    )
    except OSError as exc:
        return failure("FILE_LIST_ERROR", f"读取 data/ 文件列表失败：{exc}")

    return success(files, f"找到 {len(files)} 个受支持文件")


def get_file_info(file_name: str) -> dict[str, Any]:
    """Return metadata for one supported data file."""
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
