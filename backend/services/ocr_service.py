"""Local, bounded OCR adapter for scanned PDF pages."""

from pathlib import Path
from typing import Any

from backend.tools.excel_utils import failure, success


MAX_OCR_PAGES = 100
RENDER_SCALE = 2.0


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


def ocr_pdf(path: Path) -> dict[str, Any]:
    """Recognize real rendered PDF pages and return only validated OCR blocks."""
    try:
        rendered_pages = _render_pdf_pages(path)
        engine = _create_engine()
    except OCRDependencyError as exc:
        return failure("OCR_DEPENDENCY_MISSING", str(exc))
    except OCRServiceError as exc:
        return failure(exc.error_code, str(exc))

    blocks: list[dict[str, Any]] = []
    try:
        for page_no, image in rendered_pages:
            raw_result, _ = engine(image)
            for raw_block in raw_result or []:
                normalized = _normalize_block(page_no, raw_block)
                if normalized is not None:
                    blocks.append(normalized)
    except Exception:
        return failure("OCR_PROCESSING_ERROR", "OCR 识别过程失败")
    if not blocks:
        return failure("OCR_NO_TEXT", "OCR 未识别到可用文本")
    return success(
        {"blocks": blocks, "page_count": len(rendered_pages)},
        f"OCR 完成，共识别 {len(blocks)} 个文本块",
    )


def _render_pdf_pages(path: Path) -> list[tuple[int, Any]]:
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
        if document.page_count > MAX_OCR_PAGES:
            raise OCRServiceError(
                "OCR_PAGE_LIMIT", f"扫描 PDF 超过 OCR 页数上限 {MAX_OCR_PAGES}"
            )
        rendered = []
        matrix = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
        for page_no, page in enumerate(document, start=1):
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


class OCRServiceError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class OCRDependencyError(RuntimeError):
    pass
