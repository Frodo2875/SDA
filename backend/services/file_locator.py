"""Resolve registered files within the approved project data directories."""

from pathlib import Path, PurePosixPath

from backend import database
from backend.tools import excel_utils


class FileLocatorError(ValueError):
    """Raised when a file reference is missing or unsafe."""


def _validate_plain_value(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FileLocatorError(f"{label}不能为空")
    clean_value = value.strip()
    if (
        Path(clean_value).is_absolute()
        or "/" in clean_value
        or "\\" in clean_value
        or clean_value in {".", ".."}
    ):
        raise FileLocatorError(f"{label}不能包含路径")
    return clean_value


def _path_from_record(record: dict[str, object]) -> Path:
    raw_path = str(record.get("file_path") or "")
    if not raw_path or "\\" in raw_path:
        raise FileLocatorError("文件记录路径无效")
    relative = PurePosixPath(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise FileLocatorError("文件记录路径越界")
    parts = list(relative.parts)
    if parts and parts[0] == "data":
        parts = parts[1:]
    if len(parts) not in {1, 2} or (len(parts) == 2 and parts[0] != "uploads"):
        raise FileLocatorError("文件不在允许目录中")

    data_dir = excel_utils.DATA_DIR.resolve()
    candidate = data_dir.joinpath(*parts).resolve()
    allowed_parents = {data_dir, (data_dir / "uploads").resolve()}
    if candidate.parent not in allowed_parents:
        raise FileLocatorError("文件不在允许目录中")
    if not candidate.is_file():
        raise FileLocatorError("文件不存在")
    return candidate


def resolve_by_file_id(file_id: str) -> Path:
    """Resolve a V2 file reference by stable file ID."""
    clean_file_id = _validate_plain_value(file_id, "file_id")
    record = database.get_file_record_by_id(clean_file_id)
    if record is None:
        raise FileLocatorError("未找到对应 file_id")
    return _path_from_record(record)


def resolve_by_file_name(file_name: str) -> Path:
    """Resolve a V1-compatible plain file name through its registered record."""
    clean_file_name = _validate_plain_value(file_name, "文件名")
    record = database.get_file_record(clean_file_name)
    if record is None:
        raise FileLocatorError("未找到对应文件记录")
    return _path_from_record(record)
