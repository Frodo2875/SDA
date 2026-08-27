"""Local, bounded OCR adapter for scanned PDF pages."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.tools.excel_utils import failure, success


MAX_OCR_PAGES = 100
RENDER_SCALE = 2.0


class OCRPageResult(BaseModel):
    """Validated page checkpoint shared by OCR, indexing, and retry."""

    model_config = ConfigDict(extra="forbid")

    page_no: int = Field(ge=1)
    text: str = ""
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: Literal["success", "failed"]
    error: str | None = None
    source_type: Literal["text", "ocr"] = "ocr"
    blocks: list[dict[str, Any]] = Field(default_factory=list)


def detect_pdf_text(path: Path) -> dict[str, Any]:
    """Inspect embedded PDF text without treating an empty result as an error."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return failure("PDF_DEPENDENCY_MISSING", "缺少 pypdf，无法检测 PDF 文本")
    try:
        reader = PdfReader(path, strict=False)
        pages = [
            {"page": page_no, "text": page.extract_text() or ""}
            for page_no, page in enumerate(reader.pages, start=1)
        ]
    except Exception:
        return failure("PDF_PARSE_ERROR", "PDF 文件无法正常解析")
    return success(
        {
            "has_text": any(item["text"].strip() for item in pages),
            "page_count": len(pages),
            "pages": pages,
        },
        "PDF 文本检测完成",
    )


def detect_pdf_type(path: Path) -> dict[str, Any]:
    """Classify a PDF as text, scanned, or mixed from each page's text layer."""
    detected = detect_pdf_text(path)
    if not detected["ok"]:
        return detected
    pages = detected["data"]["pages"]
    text_pages = [int(page["page"]) for page in pages if str(page.get("text") or "").strip()]
    ocr_pages = [int(page["page"]) for page in pages if not str(page.get("text") or "").strip()]
    if text_pages and ocr_pages:
        pdf_type = "mixed"
    elif text_pages:
        pdf_type = "text"
    else:
        pdf_type = "scanned"
    return success(
        {
            **detected["data"],
            "pdf_type": pdf_type,
            "text_pages": text_pages,
            "needs_ocr_pages": ocr_pages,
        },
        "PDF 类型检测完成",
    )


def ocr_document(path: Path, pages: list[int] | None = None) -> dict[str, Any]:
    """OCR selected pages independently and retain successes when another page fails."""
    try:
        rendered_pages = (
            _render_pdf_pages(path)
            if pages is None
            else _render_pdf_pages(path, pages=pages)
        )
        engine = _create_engine()
    except OCRDependencyError as exc:
        return failure("OCR_DEPENDENCY_MISSING", str(exc))
    except OCRServiceError as exc:
        return failure(exc.error_code, str(exc))

    blocks: list[dict[str, Any]] = []
    page_results: list[dict[str, Any]] = []
    for page_no, image in rendered_pages:
        try:
            raw_result, _ = engine(image)
            page_blocks = []
            for raw_block in raw_result or []:
                normalized = _normalize_block(page_no, raw_block)
                if normalized is not None:
                    page_blocks.append(normalized)
            if not page_blocks:
                raise OCRServiceError("OCR_NO_TEXT", "OCR 未识别到可用文本")
            blocks.extend(page_blocks)
            page_results.append(_successful_page(page_no, page_blocks))
        except Exception as exc:
            message = str(exc) if isinstance(exc, OCRServiceError) else "OCR 识别过程失败"
            page_results.append(
                OCRPageResult(
                    page_no=page_no,
                    status="failed",
                    error=message,
                    source_type="ocr",
                ).model_dump()
            )
    failed_pages = [page["page_no"] for page in page_results if page["status"] == "failed"]
    succeeded = len(page_results) - len(failed_pages)
    status = "success" if not failed_pages else "partial_success" if succeeded else "failed"
    data = {
        "status": status,
        "blocks": blocks,
        "pages": page_results,
        "failed_pages": failed_pages,
        "page_count": len(rendered_pages),
    }
    if status == "failed":
        return failure("OCR_PROCESSING_ERROR", "OCR 所有指定页面均识别失败", data)
    message = (
        f"OCR 部分完成，失败页：{failed_pages}"
        if failed_pages
        else f"OCR 完成，共识别 {len(blocks)} 个文本块"
    )
    return success(data, message)


def ocr_pdf(path: Path, pages: list[int] | None = None) -> dict[str, Any]:
    """Backward-compatible entry point for full or selected-page OCR."""
    return ocr_document(path, pages=pages)


def ocr_page(path: Path, page_no: int) -> dict[str, Any]:
    """OCR exactly one 1-based page through the same bounded adapter."""
    result = ocr_document(path, pages=[int(page_no)])
    if result.get("data") is not None:
        page = (result["data"].get("pages") or [None])[0]
        result["data"] = page
    return result


def _render_pdf_pages(
    path: Path, pages: list[int] | None = None
) -> list[tuple[int, Any]]:
    try:
        import fitz
        import numpy as np
    except ImportError as exc:
        raise OCRDependencyError("缺少 PyMuPDF 或 numpy，无法渲染扫描 PDF") from exc
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise OCRServiceError("PDF_RENDER_ERROR", "扫描 PDF 无法打开") from exc
    try:
        requested = list(range(1, document.page_count + 1)) if pages is None else list(pages)
        if not requested or len(requested) > MAX_OCR_PAGES:
            raise OCRServiceError(
                "OCR_PAGE_LIMIT", f"OCR 页面数量必须在 1 到 {MAX_OCR_PAGES} 之间"
            )
        if any(not isinstance(page_no, int) or page_no < 1 or page_no > document.page_count for page_no in requested):
            raise OCRServiceError("OCR_PAGE_INVALID", "OCR 页码超出 PDF 范围")
        if len(set(requested)) != len(requested):
            raise OCRServiceError("OCR_PAGE_INVALID", "OCR 页码不能重复")
        rendered = []
        matrix = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
        for page_no in requested:
            page = document[page_no - 1]
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            channels = int(pixmap.n)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, channels
            )
            rendered.append((page_no, image))
        return rendered
    except OCRServiceError:
        raise
    except Exception as exc:
        raise OCRServiceError("PDF_RENDER_ERROR", "扫描 PDF 页面渲染失败") from exc
    finally:
        document.close()


def _create_engine() -> Any:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise OCRDependencyError("缺少 rapidocr-onnxruntime，无法执行 OCR") from exc
    try:
        return RapidOCR()
    except Exception as exc:
        raise OCRDependencyError("OCR 引擎初始化失败") from exc


def _normalize_block(page_no: int, raw_block: Any) -> dict[str, Any] | None:
    if not isinstance(raw_block, (list, tuple)) or len(raw_block) < 3:
        return None
    raw_box, raw_text, raw_confidence = raw_block[:3]
    text = str(raw_text).strip() if raw_text is not None else ""
    if not text:
        return None
    try:
        confidence = float(raw_confidence)
        points = [point for point in raw_box if len(point) >= 2]
        if not points or not 0.0 <= confidence <= 1.0:
            return None
        x_values = [float(point[0]) for point in points]
        y_values = [float(point[1]) for point in points]
    except (TypeError, ValueError):
        return None
    return {
        "page": int(page_no),
        "text": text,
        "confidence": confidence,
        "bbox": [min(x_values), min(y_values), max(x_values), max(y_values)],
    }


def _successful_page(page_no: int, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    bbox = [
        min(block["bbox"][0] for block in blocks),
        min(block["bbox"][1] for block in blocks),
        max(block["bbox"][2] for block in blocks),
        max(block["bbox"][3] for block in blocks),
    ]
    return OCRPageResult(
        page_no=page_no,
        text="\n".join(block["text"] for block in blocks),
        bbox=bbox,
        confidence=min(float(block["confidence"]) for block in blocks),
        status="success",
        error=None,
        source_type="ocr",
        blocks=blocks,
    ).model_dump()


class OCRServiceError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class OCRDependencyError(RuntimeError):
    pass
