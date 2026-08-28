"""Unified, explainable routing for supported document inputs."""

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from backend.services import ocr_service
from backend.tools.excel_utils import failure, success


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
SUPPORTED_SUFFIXES = {".xlsx", ".docx", ".pdf", *IMAGE_SUFFIXES}
MAX_IMAGE_PIXELS = 40_000_000
PREVIEW_MAX_EDGE = 1200
_GENERIC_MIME_TYPES = {"", "application/octet-stream"}
_MIME_ALIASES = {"image/jpg": "image/jpeg", "image/x-png": "image/png"}
_EXPECTED_MIME_TYPES = {
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".pdf": {"application/pdf"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png": {"image/png"},
}
_IMAGE_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}
_IMAGE_MIME_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png"}


def route_document_input(
    path: Path,
    *,
    declared_mime_type: str | None = None,
) -> dict[str, Any]:
    """Detect one supported input and select the existing structured/text/visual path."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return failure(
            "UNSUPPORTED_FILE_TYPE",
            "不支持的文件类型；仅支持 XLSX、DOCX、PDF、JPG、JPEG 和 PNG",
            {"suffix": suffix or None, "route": "SAFE_REJECT"},
        )
    mime_error = validate_declared_mime(suffix, declared_mime_type)
    if mime_error is not None:
        return mime_error

    if suffix == ".xlsx":
        return success(
            _route_data("excel", "STRUCTURED", suffix, _canonical_mime(suffix)),
            "输入已路由到结构化文档处理",
        )
    if suffix == ".docx":
        return success(
            _route_data("word", "TEXT", suffix, _canonical_mime(suffix)),
            "输入已路由到文本文档处理",
        )
    if suffix == ".pdf":
        detected = ocr_service.detect_pdf_type(path)
        if not detected["ok"]:
            return detected
        return route_pdf_detection(
            pdf_type=detected["data"]["pdf_type"],
            page_count=int(detected["data"]["page_count"]),
            text_pages=list(detected["data"]["text_pages"]),
            needs_ocr_pages=list(detected["data"]["needs_ocr_pages"]),
        )

    image = detect_image(path, expected_suffix=suffix)
    if not image["ok"]:
        return image
    return success(
        {
            **_route_data("image", "VISUAL", suffix, image["data"]["mime_type"]),
            **image["data"],
        },
        "图片输入已路由到视觉文档处理",
    )


def route_pdf_pages(pages: list[tuple[int, str]]) -> dict[str, Any]:
    """Route already-parsed PDF pages without reopening the V3 parser boundary."""
    text_pages = [int(page_no) for page_no, text in pages if str(text).strip()]
    needs_ocr_pages = [int(page_no) for page_no, text in pages if not str(text).strip()]
    if text_pages and needs_ocr_pages:
        pdf_type = "mixed"
    elif text_pages:
        pdf_type = "text"
    else:
        pdf_type = "scanned"
    return route_pdf_detection(
        pdf_type=pdf_type,
        page_count=len(pages),
        text_pages=text_pages,
        needs_ocr_pages=needs_ocr_pages,
    )


def route_pdf_detection(
    *,
    pdf_type: str,
    page_count: int,
    text_pages: list[int],
    needs_ocr_pages: list[int],
) -> dict[str, Any]:
    """Apply the shared PDF-to-Text/Visual decision to one trusted detection."""
    route = "TEXT" if pdf_type == "text" else "VISUAL"
    return success(
        {
            **_route_data("pdf", route, ".pdf", "application/pdf"),
            "pdf_type": pdf_type,
            "page_count": int(page_count),
            "text_pages": list(text_pages),
            "needs_ocr_pages": list(needs_ocr_pages),
        },
        "PDF 输入类型检测与路由完成",
    )


def detect_image(path: Path, *, expected_suffix: str | None = None) -> dict[str, Any]:
    """Validate JPEG/PNG bytes and return bounded, content-derived metadata."""
    try:
        with Image.open(path) as candidate:
            candidate.verify()
        with Image.open(path) as candidate:
            image_format = str(candidate.format or "").upper()
            width, height = candidate.size
            if image_format not in _IMAGE_MIME_TYPES:
                return failure(
                    "UNSUPPORTED_IMAGE_FORMAT",
                    "图片内容不是受支持的 JPEG 或 PNG 格式",
                )
            if expected_suffix and _IMAGE_FORMATS.get(expected_suffix) != image_format:
                return failure(
                    "INVALID_FILE_CONTENT",
                    "图片内容与文件扩展名不一致",
                )
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                return failure(
                    "IMAGE_SIZE_INVALID",
                    f"图片像素数量必须在 1 到 {MAX_IMAGE_PIXELS} 之间",
                )
            candidate.load()
    except (
        OSError,
        UnidentifiedImageError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
    ):
        return failure(
            "INVALID_FILE_CONTENT",
            "文件不是可正常打开的 JPEG 或 PNG 图片，请检查文件是否损坏",
        )
    return success(
        {
            "image_format": image_format,
            "mime_type": _IMAGE_MIME_TYPES[image_format],
            "width": int(width),
            "height": int(height),
        },
        "图片类型检测完成",
    )


def build_image_preview(path: Path) -> dict[str, Any]:
    """Build a bounded base64 thumbnail for the existing Workspace preview API."""
    detected = detect_image(path, expected_suffix=path.suffix.lower())
    if not detected["ok"]:
        return detected
    try:
        with Image.open(path) as source:
            source.load()
            preview = source.copy()
        preview.thumbnail((PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE))
        image_format = detected["data"]["image_format"]
        if image_format == "JPEG" and preview.mode not in {"RGB", "L"}:
            preview = preview.convert("RGB")
        output = BytesIO()
        preview.save(output, format=image_format, optimize=True)
    except (OSError, ValueError):
        return failure("FILE_PREVIEW_ERROR", "图片预览生成失败")
    return success(
        {
            "preview_type": "image",
            "content_base64": base64.b64encode(output.getvalue()).decode("ascii"),
            "mime_type": detected["data"]["mime_type"],
            "width": detected["data"]["width"],
            "height": detected["data"]["height"],
            "preview_width": int(preview.width),
            "preview_height": int(preview.height),
            "truncated": preview.size
            != (detected["data"]["width"], detected["data"]["height"]),
        },
        "图片预览生成成功",
    )


def _route_data(
    file_type: str, route: str, suffix: str, mime_type: str
) -> dict[str, Any]:
    return {
        "file_type": file_type,
        "route": route,
        "suffix": suffix,
        "mime_type": mime_type,
    }


def _canonical_mime(suffix: str) -> str:
    return next(iter(_EXPECTED_MIME_TYPES[suffix]))


def validate_declared_mime(
    suffix: str, declared_mime_type: str | None
) -> dict[str, Any] | None:
    """Reject a specific mismatched MIME while retaining generic-upload compatibility."""
    declared = _MIME_ALIASES.get(
        str(declared_mime_type or "").split(";", 1)[0].strip().casefold(),
        str(declared_mime_type or "").split(";", 1)[0].strip().casefold(),
    )
    if declared in _GENERIC_MIME_TYPES or declared in _EXPECTED_MIME_TYPES[suffix]:
        return None
    return failure(
        "UNSUPPORTED_MIME_TYPE",
        f"声明的 MIME 类型与 {suffix} 文件不匹配",
        {
            "declared_mime_type": declared,
            "expected_mime_types": sorted(_EXPECTED_MIME_TYPES[suffix]),
            "route": "SAFE_REJECT",
        },
    )
