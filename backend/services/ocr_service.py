"""Local, bounded Visual OCR adapter for images and selected PDF pages."""

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.services.visual_safety import assess_visual_document_data
from backend.tools.excel_utils import failure, success


MAX_OCR_PAGES = 100
RENDER_SCALE = 2.0
RECOGNITION_TYPES = {"printed", "handwritten", "mixed", "unknown"}
REGION_STATUSES = {"success", "low_confidence", "empty", "failed"}


class HandwritingThresholds(BaseModel):
    """Model-independent confidence policy for handwriting safety decisions."""

    model_config = ConfigDict(extra="forbid")

    usable_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    review_confidence: float = Field(default=0.80, ge=0.0, le=1.0)
    key_field_confidence: float = Field(default=0.90, ge=0.0, le=1.0)


def get_handwriting_thresholds() -> HandwritingThresholds:
    """Load normalized policy thresholds without coupling to any model's raw scale."""
    try:
        policy = HandwritingThresholds(
            usable_confidence=float(os.getenv("HANDWRITING_USABLE_CONFIDENCE", "0.55")),
            review_confidence=float(os.getenv("HANDWRITING_REVIEW_CONFIDENCE", "0.80")),
            key_field_confidence=float(os.getenv("HANDWRITING_KEY_FIELD_CONFIDENCE", "0.90")),
        )
    except (TypeError, ValueError) as exc:
        raise OCRServiceError("HANDWRITING_CONFIG_INVALID", "手写识别置信度配置无效") from exc
    if not (
        policy.usable_confidence
        <= policy.review_confidence
        <= policy.key_field_confidence
    ):
        raise OCRServiceError(
            "HANDWRITING_CONFIG_INVALID",
            "手写识别阈值必须满足 usable <= review <= key_field",
        )
    return policy


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


class OCRRegionResult(BaseModel):
    """One persisted OCR region; empty/failed regions never invent text."""

    model_config = ConfigDict(extra="forbid")

    file_id: str | None = Field(default=None, min_length=1, max_length=128)
    page_no: int | None = Field(default=None, ge=1)
    image_no: int | None = Field(default=None, ge=1)
    region_id: str = Field(min_length=1, max_length=128)
    text: str = ""
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    recognition_type: Literal["printed", "handwritten", "mixed", "unknown"] = "printed"
    source_model: str | None = Field(default=None, min_length=1, max_length=128)
    source_parser: str = Field(min_length=1, max_length=128)
    status: Literal["success", "low_confidence", "empty", "failed"]
    error: str | None = None
    key_field_type: Literal["name", "student_id", "phone", "amount", "date"] | None = None
    review_required: bool = False
    review_reason: str | None = None
    safe_for_high_impact: bool = True
    safe_for_identity_match: bool = True
    conflict_sources: list[dict[str, Any]] = Field(default_factory=list)
    visual_block_type: Literal[
        "title", "paragraph", "table", "image", "signature", "stamp", "unknown"
    ] | None = None
    table_id_hint: str | None = Field(default=None, min_length=1, max_length=128)
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)
    row_span: int | None = Field(default=None, ge=1)
    column_span: int | None = Field(default=None, ge=1)
    table_bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    table_structure_hint: Literal[
        "grid", "merged_cells", "borderless_hand_drawn", "unknown"
    ] | None = None
    trust_level: Literal["untrusted_document_data"] = "untrusted_document_data"
    instruction_authority: Literal["none"] = "none"
    approval_authority: Literal["none"] = "none"
    can_trigger_tool: bool = False
    can_change_tool_risk: bool = False
    can_approve: bool = False
    detected_untrusted_patterns: list[str] = Field(default_factory=list)


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
    """Backward-compatible Visual OCR entry for PDF or image content."""
    return ocr_visual(path, pages=pages)


def ocr_visual(
    path: Path,
    pages: list[int] | None = None,
    *,
    file_id: str | None = None,
) -> dict[str, Any]:
    """OCR image inputs or selected PDF pages with one normalized region contract."""
    is_image = path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    try:
        if is_image:
            if pages is not None and pages != [1]:
                return failure("OCR_PAGE_INVALID", "图片 OCR 只支持 image_no/page_no 1")
            rendered_pages = [(1, _render_image(path))]
        else:
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
            page_blocks: list[dict[str, Any]] = []
            for region_index, raw_block in enumerate(raw_result or [], start=1):
                normalized = _normalize_block(
                    page_no,
                    raw_block,
                    region_index=region_index,
                    file_id=file_id,
                    image_input=is_image,
                )
                if normalized is not None:
                    page_blocks.append(normalized)
            successful = [block for block in page_blocks if block["status"] == "success"]
            if successful:
                blocks.extend(page_blocks)
                page_results.append(_successful_page(page_no, page_blocks))
            else:
                empty_regions = page_blocks or [
                    _empty_or_failed_region(
                        page_no=page_no,
                        region_index=1,
                        file_id=file_id,
                        image_input=is_image,
                        status="empty",
                        error="OCR_NO_TEXT",
                    )
                ]
                blocks.extend(empty_regions)
                page_results.append(
                    OCRPageResult(
                        page_no=page_no,
                        status="failed",
                        error="OCR 未识别到可用文本",
                        source_type="ocr",
                        blocks=empty_regions,
                    ).model_dump()
                )
        except Exception as exc:
            message = str(exc) if isinstance(exc, OCRServiceError) else "OCR 识别过程失败"
            failed_region = _empty_or_failed_region(
                page_no=page_no,
                region_index=1,
                file_id=file_id,
                image_input=is_image,
                status="failed",
                error=message,
            )
            blocks.append(failed_region)
            page_results.append(
                OCRPageResult(
                    page_no=page_no,
                    status="failed",
                    error=message,
                    source_type="ocr",
                    blocks=[failed_region],
                ).model_dump()
            )
    failed_pages = [page["page_no"] for page in page_results if page["status"] == "failed"]
    failed_region_ids = [
        region["region_id"] for region in blocks
        if region.get("status") in {"low_confidence", "empty", "failed"}
    ]
    succeeded = len(page_results) - len(failed_pages)
    status = (
        "failed" if not succeeded
        else "partial_success" if failed_pages or failed_region_ids
        else "success"
    )
    recognition_types = sorted({str(region["recognition_type"]) for region in blocks})
    data = {
        "status": status,
        "blocks": [
            {
                "page": region["page_no"],
                "text": region["text"],
                "confidence": region["confidence"],
                "bbox": region["bbox"],
            }
            for region in blocks
            if region.get("status") == "success"
        ],
        "regions": blocks,
        "pages": page_results,
        "failed_pages": failed_pages,
        "failed_region_ids": failed_region_ids,
        "recognition_types": recognition_types,
        "recognition_type": (
            recognition_types[0] if len(recognition_types) == 1 else "mixed"
        ),
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
    return ocr_visual(path, pages=pages)


def ocr_image(path: Path, *, file_id: str | None = None) -> dict[str, Any]:
    """OCR one JPG/JPEG/PNG through the shared Visual OCR adapter."""
    return ocr_visual(path, file_id=file_id)


def retry_visual_regions(
    path: Path,
    region_requests: list[dict[str, Any]],
    *,
    file_id: str | None = None,
) -> dict[str, Any]:
    """Retry only requested region crops through the same OCR normalization path."""
    if not region_requests:
        return failure("OCR_REGION_INVALID", "至少需要一个待重试 region")
    normalized_requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for request in region_requests:
        region_id = str(request.get("region_id") or "")
        page_no = request.get("page_no") or request.get("image_no")
        bbox = _normalized_bbox(request.get("bbox"))
        if (
            not region_id or region_id in seen or not isinstance(page_no, int)
            or page_no < 1 or bbox is None or bbox[0] == bbox[2] or bbox[1] == bbox[3]
        ):
            return failure("OCR_REGION_INVALID", "region 标识、页码或 bbox 无效")
        seen.add(region_id)
        normalized_requests.append({
            "region_id": region_id,
            "page_no": page_no,
            "image_no": request.get("image_no"),
            "bbox": bbox,
        })
    is_image = path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    page_numbers = sorted({int(item["page_no"]) for item in normalized_requests})
    try:
        rendered = (
            {1: _render_image(path)}
            if is_image else dict(_render_pdf_pages(path, pages=page_numbers))
        )
        engine = _create_engine()
    except OCRDependencyError as exc:
        return failure("OCR_DEPENDENCY_MISSING", str(exc))
    except OCRServiceError as exc:
        return failure(exc.error_code, str(exc))

    regions: list[dict[str, Any]] = []
    failed_region_ids: list[str] = []
    for request in normalized_requests:
        try:
            image = rendered[int(request["page_no"])]
            x1, y1, x2, y2 = (int(round(value)) for value in request["bbox"])
            crop = image[y1:y2, x1:x2]
            if getattr(crop, "size", 0) == 0:
                raise OCRServiceError("OCR_REGION_INVALID", "region bbox 超出图像范围")
            raw_result, _ = engine(crop)
            candidates = [
                item for index, raw in enumerate(raw_result or [], start=1)
                if (item := _normalize_block(
                    int(request["page_no"]), raw, region_index=index,
                    file_id=file_id, image_input=is_image,
                )) is not None
            ]
            successful = [item for item in candidates if item["status"] == "success"]
            if len(successful) == 1:
                retried = dict(successful[0])
                retried.update({
                    "region_id": request["region_id"],
                    "bbox": request["bbox"],
                    "page_no": request["page_no"],
                    "image_no": request["page_no"] if is_image else request["image_no"],
                })
            elif len(successful) > 1:
                retried = _merge_retry_segments(request, successful, file_id, is_image)
            else:
                retried = _retry_failed_region(
                    request, file_id, is_image, "OCR_REGION_NO_USABLE_TEXT",
                    candidate=candidates[0] if candidates else None,
                )
                failed_region_ids.append(request["region_id"])
        except Exception as exc:
            error = str(exc) if isinstance(exc, OCRServiceError) else "OCR region 重试失败"
            retried = _retry_failed_region(request, file_id, is_image, error)
            failed_region_ids.append(request["region_id"])
        regions.append(retried)
    status = (
        "success" if not failed_region_ids
        else "failed" if len(failed_region_ids) == len(regions)
        else "partial_success"
    )
    data = {
        "status": status,
        "regions": regions,
        "failed_region_ids": failed_region_ids,
        "retried_region_ids": [item["region_id"] for item in normalized_requests],
    }
    if status == "failed":
        return failure("OCR_REGION_RETRY_FAILED", "所有指定 region 重试失败", data)
    return success(data, "OCR region 重试完成")


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


def _render_image(path: Path) -> Any:
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        raise OCRDependencyError("缺少 Pillow 或 numpy，无法读取图片") from exc
    try:
        with Image.open(path) as source:
            source.load()
            return np.asarray(source.convert("RGB"))
    except Exception as exc:
        raise OCRServiceError("IMAGE_RENDER_ERROR", "图片无法进入 OCR") from exc


def _create_engine() -> Any:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise OCRDependencyError("缺少 rapidocr-onnxruntime，无法执行 OCR") from exc
    try:
        return RapidOCR()
    except Exception as exc:
        raise OCRDependencyError("OCR 引擎初始化失败") from exc


def _normalize_block(
    page_no: int,
    raw_block: Any,
    *,
    region_index: int,
    file_id: str | None,
    image_input: bool,
) -> dict[str, Any] | None:
    parsed = _parse_raw_block(raw_block)
    if parsed is None:
        return None
    raw_box = parsed["bbox"]
    try:
        confidence = float(parsed["confidence"])
        bbox = _normalized_bbox(raw_box)
        if bbox is None or not 0.0 <= confidence <= 1.0:
            return None
    except (TypeError, ValueError):
        return None
    text = str(parsed.get("text") or "").strip()
    recognition_type = str(parsed.get("recognition_type") or "printed")
    if recognition_type not in RECOGNITION_TYPES:
        recognition_type = "unknown"
    conflict_sources = _normalized_conflict_sources(parsed.get("candidates") or [])
    distinct = {item["text"].strip() for item in conflict_sources if item["text"].strip()}
    if len(distinct) > 1:
        text = ""
        recognition_type = (
            "mixed" if len({item["recognition_type"] for item in conflict_sources}) > 1
            else recognition_type
        )
        parsed["status"] = "low_confidence"
        parsed["error"] = "RECOGNITION_MODEL_CONFLICT"
    region = OCRRegionResult(
        file_id=file_id,
        page_no=int(page_no),
        image_no=int(page_no) if image_input else None,
        region_id=_region_id(file_id, page_no, region_index, bbox, text),
        text=text,
        bbox=bbox,
        confidence=confidence,
        recognition_type=recognition_type,
        source_model=str(parsed.get("source_model") or "rapidocr-onnxruntime"),
        source_parser=str(parsed.get("source_parser") or "rapidocr"),
        status=str(parsed.get("status") or ("success" if text else "empty")),
        error=parsed.get("error") if text or conflict_sources else "OCR_EMPTY_REGION",
        conflict_sources=conflict_sources if len(distinct) > 1 else [],
        visual_block_type=parsed.get("visual_block_type") or parsed.get("block_type"),
        table_id_hint=parsed.get("table_id_hint"),
        row_index=parsed.get("row_index"),
        column_index=parsed.get("column_index"),
        row_span=parsed.get("row_span"),
        column_span=parsed.get("column_span"),
        table_bbox=parsed.get("table_bbox"),
        table_structure_hint=parsed.get("table_structure_hint"),
    )
    return _apply_handwriting_safety(region).model_dump()


def _successful_page(page_no: int, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [block for block in blocks if block.get("status") == "success"]
    bbox = [
        min(block["bbox"][0] for block in successful),
        min(block["bbox"][1] for block in successful),
        max(block["bbox"][2] for block in successful),
        max(block["bbox"][3] for block in successful),
    ]
    return OCRPageResult(
        page_no=page_no,
        text="\n".join(block["text"] for block in successful),
        bbox=bbox,
        confidence=min(float(block["confidence"]) for block in successful),
        status="success",
        error=None,
        source_type="ocr",
        blocks=blocks,
    ).model_dump()


def normalize_region_record(
    *,
    file_id: str,
    page_no: int,
    region_index: int,
    region: dict[str, Any],
    image_input: bool = False,
) -> dict[str, Any]:
    """Upgrade legacy OCR blocks to the V4.2 region contract before persistence."""
    text = str(region.get("text") or "")
    bbox = region.get("bbox")
    confidence = region.get("confidence")
    raw_status = str(region.get("status") or ("success" if text else "empty"))
    status = raw_status if raw_status in REGION_STATUSES else "failed"
    recognition_type = str(region.get("recognition_type") or "printed")
    if recognition_type not in RECOGNITION_TYPES:
        recognition_type = "unknown"
    normalized = OCRRegionResult(
        file_id=file_id,
        page_no=int(region.get("page_no") or region.get("page") or page_no),
        image_no=(
            int(region.get("image_no") or page_no)
            if image_input or region.get("image_no") is not None else None
        ),
        region_id=str(region.get("region_id") or _region_id(
            file_id, page_no, region_index, bbox, text
        )),
        text=text,
        bbox=bbox,
        confidence=confidence,
        recognition_type=recognition_type,
        source_model=str(region.get("source_model") or "rapidocr-onnxruntime"),
        source_parser=str(region.get("source_parser") or "rapidocr"),
        status=status,
        error=region.get("error"),
        key_field_type=region.get("key_field_type"),
        review_required=bool(region.get("review_required")),
        review_reason=region.get("review_reason"),
        safe_for_high_impact=bool(region.get("safe_for_high_impact", True)),
        safe_for_identity_match=bool(region.get("safe_for_identity_match", True)),
        conflict_sources=list(region.get("conflict_sources") or []),
        visual_block_type=region.get("visual_block_type") or region.get("block_type"),
        table_id_hint=region.get("table_id_hint"),
        row_index=region.get("row_index"),
        column_index=region.get("column_index"),
        row_span=region.get("row_span"),
        column_span=region.get("column_span"),
        table_bbox=region.get("table_bbox"),
        table_structure_hint=region.get("table_structure_hint"),
    )
    return _apply_handwriting_safety(normalized).model_dump()


def _empty_or_failed_region(
    *,
    page_no: int,
    region_index: int,
    file_id: str | None,
    image_input: bool,
    status: Literal["empty", "failed"],
    error: str,
) -> dict[str, Any]:
    return OCRRegionResult(
        file_id=file_id,
        page_no=page_no,
        image_no=page_no if image_input else None,
        region_id=_region_id(file_id, page_no, region_index, None, ""),
        text="",
        bbox=None,
        confidence=None,
        recognition_type="printed",
        source_model="rapidocr-onnxruntime",
        source_parser="rapidocr",
        status=status,
        error=error,
    ).model_dump()


def _parse_raw_block(raw_block: Any) -> dict[str, Any] | None:
    if isinstance(raw_block, dict):
        if "bbox" not in raw_block or "confidence" not in raw_block:
            return None
        return dict(raw_block)
    if not isinstance(raw_block, (list, tuple)) or len(raw_block) < 3:
        return None
    metadata = dict(raw_block[3]) if len(raw_block) > 3 and isinstance(raw_block[3], dict) else {}
    return {
        **metadata,
        "bbox": raw_block[0],
        "text": raw_block[1],
        "confidence": raw_block[2],
    }


def _normalized_bbox(raw_box: Any) -> list[float] | None:
    if not isinstance(raw_box, (list, tuple)) or len(raw_box) < 4:
        return None
    if all(isinstance(value, (int, float)) for value in raw_box[:4]):
        x1, y1, x2, y2 = (float(value) for value in raw_box[:4])
        return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
    try:
        points = [point for point in raw_box if len(point) >= 2]
        x_values = [float(point[0]) for point in points]
        y_values = [float(point[1]) for point in points]
    except (TypeError, ValueError):
        return None
    if not x_values:
        return None
    return [min(x_values), min(y_values), max(x_values), max(y_values)]


def _normalized_conflict_sources(candidates: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            confidence = float(candidate.get("confidence"))
        except (TypeError, ValueError):
            continue
        if not 0.0 <= confidence <= 1.0:
            continue
        recognition_type = str(candidate.get("recognition_type") or "unknown")
        normalized.append({
            "text": str(candidate.get("text") or ""),
            "confidence": confidence,
            "recognition_type": (
                recognition_type if recognition_type in RECOGNITION_TYPES else "unknown"
            ),
            "source_model": str(candidate.get("source_model") or "unknown"),
            "source_parser": str(candidate.get("source_parser") or "unknown"),
        })
    return normalized


def _apply_handwriting_safety(region: OCRRegionResult) -> OCRRegionResult:
    policy = get_handwriting_thresholds()
    searchable = " ".join(
        [region.text, *(str(item.get("text") or "") for item in region.conflict_sources)]
    )
    key_field_type = region.key_field_type or _detect_key_field_type(searchable)
    is_visual_uncertain = region.recognition_type in {"handwritten", "mixed", "unknown"}
    conflict = bool(region.conflict_sources)
    confidence = region.confidence
    status = region.status
    reasons: list[str] = []
    visual_security = assess_visual_document_data(
        searchable,
        source_type=region.recognition_type,
        confidence=region.confidence,
        review_required=region.review_required,
    )
    if status in {"empty", "failed"}:
        return region.model_copy(update={
            "key_field_type": key_field_type,
            "safe_for_high_impact": False,
            "safe_for_identity_match": False,
            "detected_untrusted_patterns": visual_security["detected_patterns"],
        })
    if is_visual_uncertain and confidence is not None and confidence < policy.usable_confidence:
        status = "low_confidence"
        reasons.append("HANDWRITING_BELOW_USABLE_CONFIDENCE")
    if is_visual_uncertain and (
        confidence is None or confidence < policy.review_confidence
    ):
        reasons.append("HANDWRITING_REVIEW_REQUIRED")
    if region.recognition_type == "unknown":
        reasons.append("UNKNOWN_RECOGNITION_TYPE")
    if key_field_type and is_visual_uncertain and (
        confidence is None or confidence < policy.key_field_confidence
    ):
        reasons.append("LOW_CONFIDENCE_KEY_FIELD")
    if conflict:
        status = "low_confidence"
        reasons.append("RECOGNITION_MODEL_CONFLICT")
    if visual_security["detected_patterns"]:
        reasons.append("VISUAL_UNTRUSTED_INSTRUCTION_LIKE_DATA")
    review_required = region.review_required or bool(reasons)
    identity_field = key_field_type in {"name", "student_id", "phone"}
    return region.model_copy(update={
        "status": status,
        "key_field_type": key_field_type,
        "review_required": review_required,
        "review_reason": region.review_reason or (";".join(dict.fromkeys(reasons)) or None),
        "safe_for_high_impact": (
            region.safe_for_high_impact
            and status == "success"
            and not review_required
            and not visual_security["detected_patterns"]
        ),
        "safe_for_identity_match": (
            region.safe_for_identity_match
            and not (identity_field and review_required)
            and not conflict
        ),
        "detected_untrusted_patterns": visual_security["detected_patterns"],
    })


def _detect_key_field_type(text: str) -> str | None:
    patterns = (
        ("student_id", r"(?:学号|学生编号)\s*[:：]?\s*[A-Za-z0-9-]+"),
        ("phone", r"(?:电话|手机|联系方式)\s*[:：]?\s*[0-9*+()\s-]{6,}"),
        ("amount", r"(?:金额|费用|合计)\s*[:：]?\s*[¥￥]?\s*[0-9,.]+"),
        ("date", r"(?:日期|时间)\s*[:：]?\s*[0-9年月日./-]+"),
        ("name", r"(?:姓名|名字)\s*[:：]?\s*[^\s,，;；]{1,20}"),
    )
    for field_type, pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return field_type
    return None


def _merge_retry_segments(
    request: dict[str, Any],
    segments: list[dict[str, Any]],
    file_id: str | None,
    image_input: bool,
) -> dict[str, Any]:
    recognition_types = {str(item["recognition_type"]) for item in segments}
    merged = OCRRegionResult(
        file_id=file_id,
        page_no=int(request["page_no"]),
        image_no=int(request["page_no"]) if image_input else request.get("image_no"),
        region_id=str(request["region_id"]),
        text="\n".join(str(item["text"]) for item in segments),
        bbox=list(request["bbox"]),
        confidence=min(float(item["confidence"]) for item in segments),
        recognition_type=(
            next(iter(recognition_types)) if len(recognition_types) == 1 else "mixed"
        ),
        source_model="+".join(dict.fromkeys(str(item["source_model"]) for item in segments)),
        source_parser="visual-region-retry",
        status="success",
    )
    return _apply_handwriting_safety(merged).model_dump()


def _retry_failed_region(
    request: dict[str, Any],
    file_id: str | None,
    image_input: bool,
    error: str,
    *,
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = candidate or {}
    return OCRRegionResult(
        file_id=file_id,
        page_no=int(request["page_no"]),
        image_no=int(request["page_no"]) if image_input else request.get("image_no"),
        region_id=str(request["region_id"]),
        text=str(source.get("text") or ""),
        bbox=list(request["bbox"]),
        confidence=source.get("confidence"),
        recognition_type=str(source.get("recognition_type") or "unknown"),
        source_model=str(source.get("source_model") or "unknown"),
        source_parser=str(source.get("source_parser") or "visual-region-retry"),
        status="failed",
        error=error,
        review_required=True,
        review_reason="OCR_REGION_RETRY_FAILED",
        safe_for_high_impact=False,
        safe_for_identity_match=False,
        conflict_sources=list(source.get("conflict_sources") or []),
    ).model_dump()


def _region_id(
    file_id: str | None,
    page_no: int,
    region_index: int,
    bbox: Any,
    text: str,
) -> str:
    payload = f"{file_id or 'unbound'}:{page_no}:{region_index}:{bbox}:{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class OCRServiceError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class OCRDependencyError(RuntimeError):
    pass
