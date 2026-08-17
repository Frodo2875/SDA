"""Validation and safe persistence for user-uploaded Office documents."""

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from docx import Document
from openpyxl import load_workbook

from backend import database
from backend.tools import excel_utils
from backend.tools.excel_utils import failure, success


SUPPORTED_UPLOADS = {".xlsx": "excel", ".docx": "word"}


def _safe_file_name(file_name: str) -> tuple[str | None, dict[str, Any] | None]:
    if not isinstance(file_name, str) or not file_name.strip():
        return None, failure("INVALID_FILE_NAME", "上传文件名不能为空")
    clean_name = file_name.strip()
    if (
        clean_name in {".", ".."}
        or "/" in clean_name
        or "\\" in clean_name
        or "\x00" in clean_name
        or Path(clean_name).name != clean_name
    ):
        return None, failure("INVALID_FILE_NAME", "文件名不能包含目录路径")
    if Path(clean_name).suffix.lower() not in SUPPORTED_UPLOADS:
        return None, failure(
            "UNSUPPORTED_FILE_TYPE",
            "仅支持 .xlsx 和 .docx 文件，暂不支持 PDF",
        )
    return clean_name, None


def _validate_document(path: Path, suffix: str) -> dict[str, Any] | None:
    try:
        if suffix == ".xlsx":
            workbook = load_workbook(path, read_only=True, data_only=True)
            workbook.close()
        else:
            Document(path)
    except Exception:
        label = "Excel" if suffix == ".xlsx" else "Word"
        return failure(
            "INVALID_FILE_CONTENT",
            f"文件不是可正常打开的 {label} 文档，请检查文件是否损坏或扩展名是否正确",
        )
    return None


def save_uploaded_file(file_name: str, content: bytes) -> dict[str, Any]:
    """Validate and save one new upload without ever overwriting a file."""
    clean_name, name_error = _safe_file_name(file_name)
    if name_error is not None:
        return name_error
    if not isinstance(content, bytes) or not content:
        return failure("EMPTY_UPLOAD", "上传文件不能为空")

    data_dir = excel_utils.DATA_DIR
    upload_dir = data_dir / "uploads"
    destination = upload_dir / clean_name
    if (data_dir / clean_name).exists() or destination.exists():
        return failure("FILE_ALREADY_EXISTS", f"同名文件已存在：{clean_name}，禁止覆盖")
    if database.get_file_record(clean_name) is not None:
        return failure("FILE_ALREADY_EXISTS", f"同名文件已登记：{clean_name}，禁止覆盖")

    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = destination.suffix.lower()
    temporary_path: Path | None = None
    destination_created = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=upload_dir,
            prefix=".upload-",
            suffix=suffix,
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_path = Path(temporary_file.name)

        validation_error = _validate_document(temporary_path, suffix)
        if validation_error is not None:
            return validation_error

        try:
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return failure(
                "FILE_ALREADY_EXISTS", f"同名文件已存在：{clean_name}，禁止覆盖"
            )
        destination_created = True
        with os.fdopen(descriptor, "wb") as output_file, temporary_path.open("rb") as source:
            shutil.copyfileobj(source, output_file)
            output_file.flush()
            os.fsync(output_file.fileno())

        file_type = SUPPORTED_UPLOADS[suffix]
        database.register_file(
            file_name=clean_name,
            file_type=file_type,
            file_path=f"data/uploads/{clean_name}",
            status="active",
            writable=file_type == "word",
        )
    except sqlite3.IntegrityError:
        if destination_created:
            destination.unlink(missing_ok=True)
        return failure("FILE_ALREADY_EXISTS", f"同名文件已登记：{clean_name}，禁止覆盖")
    except sqlite3.Error:
        if destination_created:
            destination.unlink(missing_ok=True)
        return failure("FILE_REGISTER_ERROR", "文件登记到数据库失败，未保留上传文件")
    except OSError:
        if destination_created:
            destination.unlink(missing_ok=True)
        return failure("FILE_SAVE_ERROR", "上传文件保存失败")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    file_record = database.get_file_record(clean_name)
    return success(
        {
            "file_id": file_record["file_id"] if file_record else None,
            "file_name": clean_name,
            "file_type": file_type,
            "path": f"data/uploads/{clean_name}",
            "size": len(content),
            "exists": True,
            "status": "active",
        },
        "文件上传并校验成功",
    )
