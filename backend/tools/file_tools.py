"""Tools for inspecting supported files in the project data directory."""

from typing import Any

from backend.tools import excel_utils
from backend.tools.excel_utils import failure, resolve_data_file, success


SUPPORTED_SUFFIXES = {".xlsx", ".docx"}
FILE_TYPES = {".xlsx": "excel", ".docx": "word"}


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
                    files.append(
                        {
                            "file_name": path.name,
                            "file_type": FILE_TYPES[path.suffix.lower()],
                            "path": f"data/{relative_path}",
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
