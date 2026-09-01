"""Local PDF/Word parsing, deterministic chunking, and FTS-backed retrieval."""

import hashlib
import re
import time
from pathlib import Path
from typing import Any

from docx import Document
from pydantic import ValidationError

from backend import database
from backend.document_blocks import (
    DocumentBlock,
    blocks_from_chunks,
    blocks_to_chunks,
    make_document_block,
)
from backend.evidence import build_evidence, make_evidence_id
from backend.repositories.file_repository import FileLifecycleStatus
from backend.table_structure import build_simple_table, degraded_table
from backend.services import ocr_service
from backend.services.file_lifecycle import (
    get_file_lifecycle,
    record_file_lifecycle_transition,
    resume_file_lifecycle,
    start_ocr_processing,
    start_visual_processing,
    transition_file_lifecycle,
)
from backend.services.input_router import route_document_input, route_pdf_pages
from backend.services.multiformat_parser import MultiFormatParseError, parse_local_document
from backend.services.file_locator import FileLocatorError, resolve_by_file_id
from backend.services.hybrid_retrieval import hybrid_retrieve
from backend.services.trace_service import record_trace
from backend.tool_models import FileIdArguments, RetrieveDocumentArguments
from backend.tools.excel_utils import failure, success


WORD_CHUNK_SIZE = 900
WORD_CHUNK_OVERLAP = 100
SUPPORTED_DOCUMENT_TYPES = {
    "pdf", "word", "image", "presentation", "txt", "json"
}
NO_EVIDENCE_MESSAGE = "当前材料中未找到足够依据。"
OCR_NOT_SUPPORTED_MESSAGE = "当前版本不支持扫描 PDF / OCR。"
LOW_OCR_CONFIDENCE = 0.8


def parse_pdf(
    file_id: str,
    *,
    _reprocess: bool = False,
    _ocr_pages: list[int] | None = None,
) -> dict[str, Any]:
    """Index embedded PDF text or route an image-only PDF through local OCR."""
    clean_file_id, record, error = _validated_document(file_id, expected_type="pdf")
    if error is not None:
        return error
    resume_error = _resume_failed_index(record)
    if resume_error is not None:
        return resume_error
    operation_error = _begin_candidate_build(record, reprocess=_reprocess)
    if operation_error is not None:
        return operation_error
    parse_started = time.perf_counter()
    try:
        path = resolve_by_file_id(clean_file_id)
        pages = _load_pdf_pages(path)
    except PdfDependencyError:
        _record_document_stage(record, "parse", parse_started, "failed", error_code="PDF_DEPENDENCY_MISSING")
        return _index_failure(
            record,
            "PDF_DEPENDENCY_MISSING",
            "缺少 pypdf，无法解析普通文本 PDF",
            failure_stage="parse",
        )
    except FileLocatorError as exc:
        _record_document_stage(record, "parse", parse_started, "failed", error_code="FILE_NOT_FOUND")
        return _index_failure(
            record, "FILE_NOT_FOUND", str(exc), failure_stage="parse"
        )
    except Exception:
        _record_document_stage(record, "parse", parse_started, "failed", error_code="PDF_PARSE_ERROR")
        return _index_failure(
            record,
            "PDF_PARSE_ERROR",
            "PDF 文件无法正常解析",
            failure_stage="parse",
        )

    normalized_pages = [(page_no, text.strip()) for page_no, text in pages]
    routed = route_pdf_pages(normalized_pages)
    _record_document_stage(
        record, "parse", parse_started, "success",
        metrics={"page_count": len(normalized_pages)},
    )
    pdf_type = routed["data"]["pdf_type"]
    if routed["data"]["route"] == "VISUAL":
        return _parse_ocr_pdf(
            record,
            path,
            pages=normalized_pages,
            pdf_type=pdf_type,
            requested_pages=_ocr_pages,
        )
    pending_error = _mark_index_pending(record)
    if pending_error is not None:
        return pending_error
    layout_started = time.perf_counter()
    blocks = [
        make_document_block(
            file_id=clean_file_id,
            sequence=index,
            block_type="paragraph",
            content=text,
            page_no=page_no,
            source_parser="pypdf",
        )
        for index, (page_no, text) in enumerate(normalized_pages)
    ]
    chunks = blocks_to_chunks(blocks, source_type="pdf")
    _record_document_stage(
        record, "layout", layout_started, "success",
        metrics={"block_count": len(blocks)},
    )
    return _persist_index(
        record,
        chunks,
        page_count=len(pages),
        extra_data={
            "ocr_used": False,
            "ocr_status": "not_required",
            "pdf_type": "text",
            "failed_pages": [],
            "block_count": len(blocks),
            "blocks": [block.model_dump() for block in blocks],
        },
    )


def ocr_document(
    file_id: str,
    pages: list[int] | None = None,
    region_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Process all OCR-needed pages or retry only an explicit 1-based page set."""
    clean_file_id, record, error = _validated_document(file_id)
    if error is not None:
        return error
    if pages is not None and region_ids is not None:
        return failure("OCR_RETRY_SCOPE_INVALID", "页级和 region 级重试不能同时指定")
    if region_ids is not None:
        return retry_ocr_regions(clean_file_id, region_ids)
    if record["file_type"] == "image":
        if pages is not None and pages != [1]:
            return failure("OCR_PAGE_INVALID", "图片 OCR 只支持 image_no/page_no 1")
        return _index_image(
            record,
            reprocess=True,
            requested_pages=pages,
            explicit_ocr=True,
        )
    return parse_pdf(file_id, _ocr_pages=pages)


def retry_ocr_regions(file_id: str, region_ids: list[str]) -> dict[str, Any]:
    """Retry failed/review regions and atomically activate a merged OCR candidate."""
    clean_file_id, record, error = _validated_document(file_id)
    if error is not None:
        return error
    requested = [str(region_id or "").strip() for region_id in region_ids]
    if not requested or any(not item for item in requested) or len(set(requested)) != len(requested):
        return failure("OCR_REGION_INVALID", "region_ids 必须非空且唯一")
    pages = database.get_document_ocr_pages(clean_file_id)
    by_region = {
        str(region["region_id"]): region
        for page in pages for region in page.get("blocks") or []
        if region.get("region_id")
    }
    if any(region_id not in by_region for region_id in requested):
        return failure("OCR_REGION_NOT_FOUND", "指定 region 不存在于当前有效 OCR 结果")
    retryable = [
        by_region[region_id] for region_id in requested
        if by_region[region_id].get("status") in {"low_confidence", "empty", "failed"}
        or by_region[region_id].get("review_required")
    ]
    if len(retryable) != len(requested):
        return failure("OCR_REGION_NOT_RETRYABLE", "只能重试失败、低置信度或待核对 region")
    try:
        path = resolve_by_file_id(clean_file_id)
    except FileLocatorError as exc:
        return failure("FILE_NOT_FOUND", str(exc))
    started = start_ocr_processing(clean_file_id)
    if not started["ok"]:
        return failure("OCR_STATE_ERROR", started["message"], {"file_id": clean_file_id})
    retried = ocr_service.retry_visual_regions(path, retryable, file_id=clean_file_id)
    retry_data = retried.get("data") if isinstance(retried.get("data"), dict) else {}
    replacements = {
        str(region["region_id"]): region for region in retry_data.get("regions") or []
        if region.get("region_id") and region.get("status") == "success"
    }
    if not replacements:
        failed = _index_failure(
            record,
            retried.get("error_code") or "OCR_REGION_RETRY_FAILED",
            retried.get("message") or "OCR region 重试失败",
            failure_stage="ocr",
        )
        failed["data"] = {
            **(failed.get("data") or {}),
            "failed_region_ids": list(retry_data.get("failed_region_ids") or requested),
            "retried_region_ids": requested,
        }
        return failed
    merged_pages = _merge_retried_regions(pages, replacements)
    blocks, chunks = _ocr_pages_to_index(clean_file_id, merged_pages)
    pending_error = _mark_index_pending(record)
    if pending_error is not None:
        return pending_error
    failed_region_ids = list(retry_data.get("failed_region_ids") or [])
    review_region_ids = [
        str(region["region_id"])
        for page in merged_pages for region in page.get("blocks") or []
        if region.get("review_required")
    ]
    return _persist_index(
        record,
        chunks,
        page_count=len(merged_pages),
        ocr_pages=merged_pages,
        extra_data={
            "status": "partial_success" if failed_region_ids else "indexed",
            "ocr_status": "partial_success" if failed_region_ids else "success",
            "retried_region_ids": requested,
            "failed_region_ids": failed_region_ids,
            "review_required_region_ids": review_region_ids,
            "verification_required": bool(review_region_ids),
            "page_results": merged_pages,
            "ocr_results": [
                region for page in merged_pages for region in page.get("blocks") or []
            ],
            "block_count": len(blocks),
            "blocks": [block.model_dump() for block in blocks],
        },
    )


def index_document(file_id: str) -> dict[str, Any]:
    """Build or replace the local index for one registered PDF or Word."""
    return _index_document(file_id, reprocess=False)


def reindex_document(file_id: str) -> dict[str, Any]:
    """Rebuild a document candidate and atomically replace the active index."""
    return _index_document(file_id, reprocess=False)


def reprocess_document(file_id: str) -> dict[str, Any]:
    """Reparse source content without disturbing the active parsed/indexed view."""
    return _index_document(file_id, reprocess=True)


def _index_document(file_id: str, *, reprocess: bool) -> dict[str, Any]:
    clean_file_id, record, error = _validated_document(file_id)
    if error is not None:
        return error
    if record["file_type"] == "pdf":
        return parse_pdf(clean_file_id, _reprocess=reprocess)
    if record["file_type"] == "image":
        return _index_image(record, reprocess=reprocess)
    resume_error = _resume_failed_index(record)
    if resume_error is not None:
        return resume_error
    operation_error = _begin_candidate_build(record, reprocess=reprocess)
    if operation_error is not None:
        return operation_error
    parse_started = time.perf_counter()
    parsed_document = None
    try:
        path = resolve_by_file_id(clean_file_id)
        if record["file_type"] == "word":
            chunks = _word_chunks(clean_file_id, path)
        else:
            parsed_document = parse_local_document(
                path, file_id=clean_file_id, file_name=record["file_name"]
            )
            chunks = blocks_to_chunks(
                parsed_document.blocks,
                source_type=record["file_type"],
                metadata_by_block=parsed_document.metadata_by_block,
            )
    except FileLocatorError as exc:
        _record_document_stage(record, "parse", parse_started, "failed", error_code="FILE_NOT_FOUND")
        return _index_failure(
            record, "FILE_NOT_FOUND", str(exc), failure_stage="parse"
        )
    except MultiFormatParseError as exc:
        _record_document_stage(record, "parse", parse_started, "failed", error_code="DOCUMENT_PARSE_ERROR")
        return _index_failure(
            record,
            "DOCUMENT_PARSE_ERROR",
            str(exc),
            failure_stage="parse",
        )
    except Exception:
        error_code = "WORD_PARSE_ERROR" if record["file_type"] == "word" else "DOCUMENT_PARSE_ERROR"
        _record_document_stage(record, "parse", parse_started, "failed", error_code=error_code)
        return _index_failure(
            record,
            error_code,
            "Word 文件无法正常解析" if record["file_type"] == "word" else "文档无法正常解析",
            failure_stage="parse",
        )
    if not chunks:
        _record_document_stage(record, "parse", parse_started, "failed", error_code="NO_TEXT_CONTENT")
        return _index_failure(
            record,
            "NO_TEXT_CONTENT",
            "文档中没有可索引文本",
            failure_stage="parse",
        )
    pending_error = _mark_index_pending(record)
    if pending_error is not None:
        return pending_error
    blocks = blocks_from_chunks(chunks)
    _record_document_stage(
        record, "layout", parse_started, "success",
        metrics={"block_count": len(blocks)},
    )
    return _persist_index(
        record,
        chunks,
        extra_data={
            "block_count": len(blocks),
            "blocks": [block.model_dump() for block in blocks],
            **(
                {"document": parsed_document.payload()}
                if parsed_document is not None else {}
            ),
        },
    )


def _index_image(
    record: dict[str, Any],
    *,
    reprocess: bool,
    requested_pages: list[int] | None = None,
    explicit_ocr: bool = False,
) -> dict[str, Any]:
    """OCR one image through the shared Visual pipeline and atomic index activation."""
    if requested_pages is not None and requested_pages != [1]:
        return failure("OCR_PAGE_INVALID", "图片 OCR 只支持 image_no/page_no 1")
    had_active_index = _has_active_index(record["file_id"])
    had_active_text = any(
        str(chunk.get("chunk_text") or "").strip()
        for chunk in database.get_document_chunks(record["file_id"])
    )
    resume_error = _resume_failed_index(record)
    if resume_error is not None:
        return resume_error
    operation_error = _begin_candidate_build(record, reprocess=reprocess)
    if operation_error is not None:
        return operation_error

    lifecycle = get_file_lifecycle(record["file_id"])
    if not lifecycle["ok"]:
        return lifecycle
    current = FileLifecycleStatus(lifecycle["data"]["status"])
    if current == FileLifecycleStatus.UPLOADED:
        detected = transition_file_lifecycle(
            record["file_id"], FileLifecycleStatus.DETECTING
        )
        if not detected["ok"]:
            return detected
        current = FileLifecycleStatus.DETECTING
    if current not in {
        FileLifecycleStatus.DETECTING,
        FileLifecycleStatus.REPROCESSING,
        FileLifecycleStatus.REINDEXING,
    }:
        return failure(
            "INVALID_FILE_STATE",
            "图片不处于可检测或重新处理的状态",
            {"file_id": record["file_id"], "status": current.value},
        )

    route_started = time.perf_counter()
    try:
        path = resolve_by_file_id(record["file_id"])
        routed = route_document_input(path)
    except FileLocatorError as exc:
        routed = failure("FILE_NOT_FOUND", str(exc))
    if not routed["ok"] or routed["data"].get("file_type") != "image":
        error_code = routed.get("error_code") or "IMAGE_DETECTION_ERROR"
        message = routed.get("message") or "图片类型检测失败"
        _record_document_stage(
            record, "detect", route_started, "failed", error_code=error_code
        )
        return _index_failure(
            record, error_code, message, failure_stage="detect"
        )
    _record_document_stage(
        record,
        "detect",
        route_started,
        "success",
        metrics={
            "route": "VISUAL",
            "width": routed["data"]["width"],
            "height": routed["data"]["height"],
        },
    )

    visual = start_visual_processing(record["file_id"])
    if not visual["ok"]:
        return failure(
            "VISUAL_STATE_ERROR", visual["message"], {"file_id": record["file_id"]}
        )
    visual_started = time.perf_counter()
    route_data = routed["data"]
    recognized = ocr_service.ocr_image(path, file_id=record["file_id"])
    existing = {
        page["page_no"]: page
        for page in database.get_document_ocr_pages(record["file_id"])
    }
    page = _recognized_page_results(
        recognized,
        [1],
        existing=existing,
        file_id=record["file_id"],
        image_input=True,
        preserve_existing_success=False,
    )[0]
    regions = list(page.get("blocks") or [])
    successful_regions = [
        region
        for region in regions
        if region.get("status") == "success" and str(region.get("text") or "").strip()
    ]
    failed_region_ids = [
        str(region["region_id"]) for region in regions
        if region.get("status") in {"low_confidence", "empty", "failed"}
    ]
    review_region_ids = [
        str(region["region_id"]) for region in regions if region.get("review_required")
    ]
    ocr_failed = page.get("status") == "failed" or not successful_regions
    _record_document_stage(
        record,
        "visual_ocr",
        visual_started,
        "failed" if ocr_failed else "success",
        metrics={
            "image_count": 1,
            **_visual_region_trace_metrics(regions),
        },
        error_code=recognized.get("error_code") if ocr_failed else None,
    )
    if ocr_failed and had_active_index and had_active_text:
        failed = _index_failure(
            record,
            recognized.get("error_code") or "OCR_PROCESSING_ERROR",
            recognized.get("message") or "图片 OCR 未识别到可用文本",
            failure_stage="ocr",
        )
        failed_data = failed.get("data") if isinstance(failed.get("data"), dict) else {}
        failed["data"] = {
            **failed_data,
            "failed_pages": [1],
            "page_results": [page],
            "ocr_results": regions,
        }
        return failed

    layout = transition_file_lifecycle(
        record["file_id"], FileLifecycleStatus.LAYOUT_PROCESSING
    )
    if not layout["ok"]:
        return failure(
            "LAYOUT_STATE_ERROR", layout["message"], {"file_id": record["file_id"]}
        )
    layout_started = time.perf_counter()
    blocks: list[DocumentBlock] = []
    chunks: list[dict[str, Any]] = []
    if successful_regions:
        for region in successful_regions:
            block = make_document_block(
                file_id=record["file_id"],
                sequence=len(blocks),
                block_type="paragraph",
                content=str(region["text"]),
                page_no=1,
                bbox=region.get("bbox"),
                confidence=region.get("confidence"),
                source_parser=str(region.get("source_parser") or "rapidocr"),
            )
            blocks.append(block)
            region_chunks = blocks_to_chunks(
                [block],
                source_type="ocr",
                metadata_by_block={block.block_id: {
                    "input_route": "VISUAL",
                    "mime_type": route_data["mime_type"],
                    "image_format": route_data["image_format"],
                    "width": route_data["width"],
                    "height": route_data["height"],
                    "text_extracted": True,
                    "region_id": region["region_id"],
                    "recognition_type": region["recognition_type"],
                    "source_model": region.get("source_model"),
                    **_region_safety_metadata(region),
                }},
            )
            for chunk in region_chunks:
                chunk["chunk_index"] = len(chunks)
                chunks.append(chunk)
    else:
        block = make_document_block(
            file_id=record["file_id"],
            sequence=0,
            block_type="image",
            content="",
            page_no=1,
            bbox=[0.0, 0.0, float(route_data["width"]), float(route_data["height"])],
            source_parser="visual-ocr",
            status="degraded",
            warnings=["OCR_NO_TEXT"],
        )
        blocks.append(block)
        chunks = blocks_to_chunks(
            [block],
            source_type="image",
            metadata_by_block={block.block_id: {
                "input_route": "VISUAL",
                "mime_type": route_data["mime_type"],
                "image_format": route_data["image_format"],
                "width": route_data["width"],
                "height": route_data["height"],
                "text_extracted": False,
                "ocr_status": "failed",
            }},
        )
    _record_document_stage(
        record,
        "layout",
        layout_started,
        "success",
        metrics={"block_count": len(blocks)},
    )
    pending_error = _mark_index_pending(record)
    if pending_error is not None:
        return pending_error
    return _persist_index(
        record,
        chunks,
        page_count=1,
        ocr_pages=[page],
        extra_data={
            "input_route": "VISUAL",
            "mime_type": route_data["mime_type"],
            "image_format": route_data["image_format"],
            "width": route_data["width"],
            "height": route_data["height"],
            "visual_status": "ocr_failed" if ocr_failed else "ocr_success",
            "ocr_used": True,
            "ocr_status": (
                "failed" if ocr_failed else "partial_success" if failed_region_ids else "success"
            ),
            "status": (
                "partial_success"
                if explicit_ocr and (ocr_failed or failed_region_ids) else "indexed"
            ),
            "failed_pages": [1] if ocr_failed else [],
            "failed_region_ids": failed_region_ids,
            "review_required_region_ids": review_region_ids,
            "verification_required": bool(review_region_ids),
            "page_results": [page],
            "ocr_results": regions,
            "text_extracted": bool(successful_regions),
            "block_count": len(blocks),
            "blocks": [block.model_dump() for block in blocks],
        },
    )


def retrieve_document(
    scope: dict[str, Any], query: str, top_k: int = 5
) -> dict[str, Any]:
    """Retrieve bounded local evidence; never supplement missing material."""
    try:
        arguments = RetrieveDocumentArguments.model_validate(
            {"scope": scope, "query": query, "top_k": top_k}
        )
    except ValidationError as exc:
        return failure("INVALID_RETRIEVAL_ARGUMENTS", _validation_message(exc))

    requested_ids = (
        [arguments.scope.file_id]
        if arguments.scope.file_id is not None
        else arguments.scope.file_ids
    )
    eligible: dict[str, dict[str, Any]] = {}
    for record in database.fetch_all("files"):
        if requested_ids is not None and record["file_id"] not in requested_ids:
            continue
        if (
            arguments.scope.file_type is not None
            and record["file_type"] != arguments.scope.file_type
        ):
            continue
        if (
            record["file_type"] in SUPPORTED_DOCUMENT_TYPES
            and record["lifecycle_status"] == "ready"
            and record["index_status"] == "indexed"
            and bool(record["queryable"])
        ):
            eligible[record["file_id"]] = record
    requested_page = arguments.scope.page or arguments.scope.slide
    if requested_page is not None:
        failed_page_files = [
            file_id for file_id in eligible
            if (
                (database.get_document_ocr_page(file_id, requested_page) or {}).get("status")
                == "failed"
            )
        ]
        if failed_page_files:
            message = "请求页 OCR 失败，当前证据不足，请重试该页 OCR 后再查询。"
            result = success(
                {
                    "status": "insufficient_evidence",
                    "query": arguments.query,
                    "evidence": [],
                    "failed_page": requested_page,
                    "failed_page_files": failed_page_files,
                    "result_summary": message,
                },
                message,
            )
            result.update({"evidence": [], "warnings": ["OCR_FAILED_PAGE"], "result_summary": message})
            return result
    metadata_filters = dict(arguments.scope.document_metadata or {})
    requested_year = arguments.scope.year or _explicit_year_constraint(arguments.query)
    if requested_year is not None:
        metadata_filters["year"] = requested_year
    if arguments.scope.student_id is not None:
        metadata_filters["student_id"] = arguments.scope.student_id
    retrieval = hybrid_retrieve(
        file_ids=list(eligible),
        query=arguments.query,
        limit=arguments.top_k,
        page_no=arguments.scope.page or arguments.scope.slide,
        metadata_filters=metadata_filters,
    )
    rows = retrieval["rows"]
    evidence = []
    for row in rows:
        record = eligible[row["file_id"]]
        metadata = row.get("metadata") or {}
        region = _evidence_region_for_row(row, metadata)
        block_id = metadata.get("block_id")
        table = metadata.get("table_id") or (
            f"table:{metadata['table_no']}"
            if metadata.get("table_no") is not None else None
        )
        cell = (
            metadata.get("cell_id") or
            (f"R{metadata['row_no']}C{metadata['column_no']}"
             if metadata.get("row_no") is not None
             and metadata.get("column_no") is not None else None)
        )
        evidence.append(
            build_evidence(
                evidence_id=make_evidence_id(
                    chunk_id=row["chunk_id"], block_id=block_id, query=arguments.query
                ),
                task_id=None,
                source_type="unstructured",
                file_id=row["file_id"],
                file_name=record["file_name"],
                sheet=None,
                page_no=row["page_no"],
                image_no=(metadata.get("image_no") or (region or {}).get("image_no") or 1) if record["file_type"] == "image" else None,
                chunk_id=row["chunk_id"],
                block_id=block_id,
                region_id=metadata.get("region_id") or (region or {}).get("region_id"),
                table=table,
                cell=cell,
                row_index=metadata.get("row_index", metadata.get("row_no")),
                column_index=metadata.get("column_index", metadata.get("column_no")),
                line_number=metadata.get("line_number"),
                json_path=metadata.get("json_path"),
                slide_number=metadata.get("slide_number"),
                row_number=metadata.get("row_number"),
                column_name=metadata.get("column_name"),
                bbox=metadata.get("bbox"),
                confidence=metadata.get("confidence"),
                recognition_type=metadata.get("recognition_type") or (region or {}).get("recognition_type"),
                source_parser=metadata.get("source_parser") or (metadata.get("block") or {}).get("source_parser") or (region or {}).get("source_parser"),
                source_model=metadata.get("source_model") or (region or {}).get("source_model"),
                field=None,
                record_key=None,
                value_summary=_excerpt(row["chunk_text"], arguments.query),
                text_excerpt=_excerpt(row["chunk_text"], arguments.query),
                review_required=bool(metadata.get("review_required") or (region or {}).get("review_required")),
                conflict_sources=list(metadata.get("conflict_sources") or (region or {}).get("conflict_sources") or []),
            )
        )
        evidence[-1].update({
            key: metadata.get(key)
            for key in (
                "key_field_type",
                "review_required", "review_reason", "safe_for_high_impact",
                "safe_for_identity_match",
            )
            if metadata.get(key) is not None
        })
        evidence[-1]["score"] = round(
            float(row.get("retrieval_score"))
            if row.get("retrieval_score") is not None
            else 1.0 / (1.0 + abs(float(row["rank"]))),
            6,
        )
        evidence[-1]["text_excerpt"] = evidence[-1]["value_summary"]
    low_confidence_pages = sorted({
        evidence_item["page_no"]
        for evidence_item in evidence
        if evidence_item.get("confidence") is not None
        and float(evidence_item["confidence"]) < LOW_OCR_CONFIDENCE
        and evidence_item.get("page_no") is not None
    })
    if not evidence:
        result = success(
            {
                "status": "not_found",
                "query": arguments.query,
                "evidence": [],
                "retrieval_mode": retrieval["retrieval_mode"],
                "fallback_used": retrieval["fallback_used"],
                "rerank_fallback": retrieval["rerank_fallback"],
                "fallback_reason": retrieval["fallback_reason"],
                "metadata_filters": metadata_filters,
                "keyword_candidate_count": retrieval["keyword_candidate_count"],
                "vector_candidate_count": retrieval["vector_candidate_count"],
                "top_n_count": retrieval["top_n_count"],
                "top_k_count": retrieval["top_k_count"],
                "hybrid_retrieval_duration_ms": retrieval[
                    "hybrid_retrieval_duration_ms"
                ],
                "rerank_duration_ms": retrieval["rerank_duration_ms"],
                "score_details": [],
                "result_summary": NO_EVIDENCE_MESSAGE,
            },
            NO_EVIDENCE_MESSAGE,
        )
        result.update(
            {
                "evidence": [],
                "warnings": [retrieval["warning"]] if retrieval["warning"] else [],
                "result_summary": NO_EVIDENCE_MESSAGE,
            }
        )
        return result
    result = success(
        {
            "status": "found",
            "query": arguments.query,
            "evidence": evidence,
            "retrieval_mode": retrieval["retrieval_mode"],
            "fallback_used": retrieval["fallback_used"],
            "rerank_fallback": retrieval["rerank_fallback"],
            "fallback_reason": retrieval["fallback_reason"],
            "metadata_filters": metadata_filters,
            "keyword_candidate_count": retrieval["keyword_candidate_count"],
            "vector_candidate_count": retrieval["vector_candidate_count"],
            "top_n_count": retrieval["top_n_count"],
            "top_k_count": retrieval["top_k_count"],
            "hybrid_retrieval_duration_ms": retrieval[
                "hybrid_retrieval_duration_ms"
            ],
            "rerank_duration_ms": retrieval["rerank_duration_ms"],
            "score_details": [
                {
                    "chunk_id": row["chunk_id"],
                    "keyword_score": row.get("keyword_score", 0.0),
                    "vector_score": row.get("vector_score", 0.0),
                    "combined_score": row.get("combined_score", 0.0),
                    "rerank_score": row.get("rerank_score"),
                    "final_score": row.get("retrieval_score"),
                }
                for row in rows
            ],
            "result_summary": f"找到 {len(evidence)} 条材料依据",
        },
        "文档检索完成",
    )
    warnings = [retrieval["warning"]] if retrieval["warning"] else []
    if low_confidence_pages:
        warnings.append("LOW_OCR_CONFIDENCE_REVIEW_REQUIRED")
    if any(item.get("review_required") for item in evidence):
        warnings.append("HANDWRITING_REVIEW_REQUIRED")
    result.update(
        {
            "evidence": evidence,
            "warnings": warnings,
            "low_confidence_pages": low_confidence_pages,
            "result_summary": f"找到 {len(evidence)} 条材料依据",
        }
    )
    return result


def _explicit_year_constraint(query: str) -> int | None:
    years = {
        int(value) for value in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", query)
        if 1900 <= int(value) <= 2100
    }
    return next(iter(years)) if len(years) == 1 else None


def _load_pdf_pages(path: Path) -> list[tuple[int, str]]:
    """Adapter boundary kept replaceable for the selected PDF parser."""
    detected = ocr_service.detect_pdf_text(path)
    if not detected["ok"]:
        if detected.get("error_code") == "PDF_DEPENDENCY_MISSING":
            raise PdfDependencyError
        raise PdfParseError(detected.get("message") or "PDF 文件无法正常解析")
    return [
        (int(page["page"]), str(page.get("text") or ""))
        for page in detected["data"]["pages"]
    ]


def validate_pdf_file(path: Path) -> dict[str, Any] | None:
    """Validate PDF structure; image-only PDFs are accepted for the OCR branch."""
    routed = route_pdf_file(path)
    return None if routed["ok"] else routed


def route_pdf_file(path: Path) -> dict[str, Any]:
    """Run the existing replaceable PDF adapter and apply the shared input route."""
    try:
        pages = _load_pdf_pages(path)
    except PdfDependencyError:
        return failure("PDF_DEPENDENCY_MISSING", "缺少 pypdf，无法解析普通文本 PDF")
    except Exception:
        return failure("INVALID_FILE_CONTENT", "文件不是可正常打开的 PDF 文档")
    return route_pdf_pages(pages)


def _parse_scanned_pdf(
    record: dict[str, Any], path: Path, *, page_count: int
) -> dict[str, Any]:
    return _parse_ocr_pdf(
        record,
        path,
        pages=[(page_no, "") for page_no in range(1, page_count + 1)],
        pdf_type="scanned",
        requested_pages=None,
    )


def _parse_ocr_pdf(
    record: dict[str, Any],
    path: Path,
    *,
    pages: list[tuple[int, str]],
    pdf_type: str,
    requested_pages: list[int] | None,
) -> dict[str, Any]:
    page_numbers = {page_no for page_no, _ in pages}
    if requested_pages is not None:
        if (
            not requested_pages
            or len(set(requested_pages)) != len(requested_pages)
            or any(not isinstance(page_no, int) or page_no not in page_numbers for page_no in requested_pages)
        ):
            return failure("OCR_PAGE_INVALID", "OCR 页码必须唯一且位于 PDF 范围内")
    needs_ocr = [page_no for page_no, text in pages if not text]
    selected = needs_ocr if requested_pages is None else [
        page_no for page_no in requested_pages if page_no in needs_ocr
    ]
    existing = {
        page["page_no"]: page for page in database.get_document_ocr_pages(record["file_id"])
    }
    candidate_pages: dict[int, dict[str, Any]] = {
        page_no: _text_page_result(page_no, text)
        for page_no, text in pages
        if text
    }
    for page_no in needs_ocr:
        if page_no not in selected and page_no in existing:
            candidate_pages[page_no] = existing[page_no]
        elif page_no not in selected:
            candidate_pages[page_no] = _failed_page_result(page_no, "OCR_PAGE_NOT_PROCESSED")

    started = start_ocr_processing(record["file_id"])
    if not started["ok"]:
        return failure("OCR_STATE_ERROR", started["message"], {"file_id": record["file_id"]})
    recognized: dict[str, Any] | None = None
    if selected:
        ocr_started = time.perf_counter()
        recognized = (
            ocr_service.ocr_pdf(path)
            if pdf_type == "scanned" and requested_pages is None
            else ocr_service.ocr_document(path, pages=selected)
        )
        recognized_pages = _recognized_page_results(
            recognized,
            selected,
            existing=existing,
            file_id=record["file_id"],
        )
        failed_selected = sum(
            page.get("status") == "failed" or page.get("refresh_status") == "failed"
            for page in recognized_pages
        )
        _record_document_stage(
            record,
            "ocr",
            ocr_started,
            "partial_success" if failed_selected and failed_selected < len(recognized_pages)
            else "failed" if failed_selected else "success",
            metrics={
                "total_pages": len(recognized_pages),
                "successful_pages": len(recognized_pages) - failed_selected,
                "failed_pages": failed_selected,
                **_visual_region_trace_metrics([
                    region
                    for page in recognized_pages
                    for region in page.get("blocks") or []
                ]),
            },
            error_code=(recognized or {}).get("error_code") if failed_selected else None,
        )
        candidate_pages.update({page["page_no"]: page for page in recognized_pages})

    ordered_pages = [candidate_pages[page_no] for page_no in sorted(candidate_pages)]
    failed_pages = [
        page["page_no"] for page in ordered_pages
        if page["status"] == "failed" or page.get("refresh_status") == "failed"
    ]
    failed_region_ids = [
        str(region["region_id"])
        for page in ordered_pages
        for region in page.get("blocks") or []
        if region.get("status") in {"low_confidence", "empty", "failed"}
    ]
    review_region_ids = [
        str(region["region_id"])
        for page in ordered_pages
        for region in page.get("blocks") or []
        if region.get("review_required")
    ]
    successful_pages = [page for page in ordered_pages if page["status"] == "success" and page["text"].strip()]
    if not successful_pages:
        if not _has_active_index(record["file_id"]):
            database.replace_document_ocr_pages(record["file_id"], ordered_pages)
        return _index_failure(
            record,
            (recognized or {}).get("error_code") or "OCR_PROCESSING_ERROR",
            (recognized or {}).get("message") or "OCR 所有可处理页面均识别失败",
            failure_stage="ocr",
        )
    pending_error = _mark_index_pending(record)
    if pending_error is not None:
        return pending_error
    blocks: list[DocumentBlock] = []
    chunks: list[dict[str, Any]] = []
    for page in ordered_pages:
        if page["status"] == "failed":
            chunks.append(
                _chunk(
                    file_id=record["file_id"], chunk_index=len(chunks), text="",
                    page_no=page["page_no"],
                    metadata={
                        "source_type": "ocr_failed",
                        "page_no": page["page_no"],
                        "ocr_status": "failed",
                        "error": page.get("error"),
                    },
                )
            )
            continue
        raw_blocks = [
            raw for raw in page.get("blocks") or []
            if raw.get("status", "success") == "success"
            and str(raw.get("text") or "").strip()
        ] or [{
            "page_no": page["page_no"], "text": page["text"],
            "confidence": page.get("confidence"), "bbox": page.get("bbox"),
            "source_parser": "pypdf" if page["source_type"] == "text" else "rapidocr",
        }]
        for raw in raw_blocks:
            block = make_document_block(
                file_id=record["file_id"], sequence=len(blocks), block_type="paragraph",
                content=str(raw.get("text") or ""), page_no=page["page_no"],
                confidence=(
                    raw.get("confidence")
                    if page["source_type"] == "ocr" and page["status"] == "success"
                    else None
                ),
                source_parser=str(raw.get("source_parser") or (
                    "rapidocr" if page["source_type"] == "ocr" else "pypdf"
                )),
                bbox=(
                    raw.get("bbox")
                    if page["source_type"] == "ocr" and page["status"] == "success"
                    else None
                ),
            )
            blocks.append(block)
            block_chunks = blocks_to_chunks(
                [block],
                source_type="ocr" if page["source_type"] == "ocr" else "pdf",
                metadata_by_block={block.block_id: _region_safety_metadata(raw)},
            )
            for chunk in block_chunks:
                chunk["chunk_index"] = len(chunks)
                chunks.append(chunk)
    ocr_results = [block for page in ordered_pages for block in page.get("blocks") or []]
    low_confidence_pages = [
        page["page_no"] for page in successful_pages
        if page.get("confidence") is not None and page["confidence"] < LOW_OCR_CONFIDENCE
    ]
    result = _persist_index(
        record,
        chunks,
        page_count=len(pages),
        ocr_pages=ordered_pages,
        extra_data={
            "ocr_used": True,
            "ocr_status": (
                "partial_success" if failed_pages or failed_region_ids else "success"
            ),
            "status": (
                "partial_success" if failed_pages or failed_region_ids else "indexed"
            ),
            "pdf_type": pdf_type,
            "failed_pages": failed_pages,
            "failed_region_ids": failed_region_ids,
            "review_required_region_ids": review_region_ids,
            "low_confidence_pages": low_confidence_pages,
            "verification_required": bool(low_confidence_pages or review_region_ids),
            "page_results": ordered_pages,
            "ocr_results": ocr_results,
            "block_count": len(blocks),
            "blocks": [block.model_dump() for block in blocks],
        },
    )
    if result["ok"] and failed_pages:
        result["message"] = f"OCR 部分成功，失败页：{failed_pages}；成功页面已可查询"
    return result


def _text_page_result(page_no: int, text: str) -> dict[str, Any]:
    return {
        "page_no": page_no, "text": text, "bbox": None, "confidence": 1.0,
        "status": "success", "error": None, "source_type": "text", "blocks": [],
        "updated_at": database.utc_now(),
    }


def _failed_page_result(page_no: int, error: str) -> dict[str, Any]:
    return {
        "page_no": page_no, "text": "", "bbox": None, "confidence": None,
        "status": "failed", "error": error, "source_type": "ocr", "blocks": [],
        "updated_at": database.utc_now(),
    }


def _recognized_page_results(
    result: dict[str, Any],
    selected: list[int],
    *,
    existing: dict[int, dict[str, Any]],
    file_id: str,
    image_input: bool = False,
    preserve_existing_success: bool = True,
) -> list[dict[str, Any]]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    supplied = data.get("pages") or []
    by_page = {int(page["page_no"]): dict(page) for page in supplied if page.get("page_no")}
    if not supplied:
        grouped: dict[int, list[dict[str, Any]]] = {}
        for block in data.get("blocks") or []:
            grouped.setdefault(
                int(block.get("page_no") or block.get("page")), []
            ).append(block)
        for page_no, page_blocks in grouped.items():
            by_page[page_no] = {
                "page_no": page_no,
                "text": "\n".join(str(block["text"]) for block in page_blocks),
                "bbox": _union_bbox(page_blocks),
                "confidence": min(float(block["confidence"]) for block in page_blocks),
                "status": "success", "error": None, "source_type": "ocr",
                "blocks": page_blocks,
            }
    normalized = []
    for page_no in selected:
        page = by_page.get(page_no) or _failed_page_result(
            page_no, result.get("message") or "OCR_PROCESSING_ERROR"
        )
        attempted_error = page.get("error") or result.get("message")
        if (
            preserve_existing_success
            and page.get("status") == "failed"
            and existing.get(page_no, {}).get("status") == "success"
        ):
            page = {
                **existing[page_no],
                "refresh_status": "failed",
                "refresh_error": attempted_error or "OCR_PROCESSING_ERROR",
            }
        raw_regions = list(page.get("blocks") or [])
        if not raw_regions and page.get("status") == "failed":
            raw_regions = [{
                "text": "",
                "bbox": None,
                "confidence": None,
                "status": "failed",
                "error": page.get("error") or result.get("message") or "OCR_PROCESSING_ERROR",
            }]
        regions = [
            ocr_service.normalize_region_record(
                file_id=file_id,
                page_no=page_no,
                region_index=index,
                region=region,
                image_input=image_input,
            )
            for index, region in enumerate(raw_regions, start=1)
        ]
        successful_regions = [
            region for region in regions if region["status"] == "success" and region["text"].strip()
        ]
        page = {
            **page,
            "page_no": page_no,
            "source_type": "ocr",
            "blocks": regions,
            "updated_at": database.utc_now(),
        }
        if successful_regions:
            page["text"] = "\n".join(region["text"] for region in successful_regions)
            page["bbox"] = _union_bbox(successful_regions)
            confidences = [
                float(region["confidence"])
                for region in successful_regions
                if region.get("confidence") is not None
            ]
            page["confidence"] = min(confidences) if confidences else None
            page["status"] = "success"
            page["error"] = None
        normalized.append(page)
    return normalized


def _region_safety_metadata(region: dict[str, Any]) -> dict[str, Any]:
    """Expose handwriting review controls without changing legacy printed metadata."""
    recognition_type = str(region.get("recognition_type") or "printed")
    if (
        recognition_type == "printed"
        and not region.get("review_required")
        and not region.get("conflict_sources")
    ):
        return {}
    return {
        "region_id": region.get("region_id"),
        "recognition_type": recognition_type,
        "key_field_type": region.get("key_field_type"),
        "review_required": bool(region.get("review_required")),
        "review_reason": region.get("review_reason"),
        "safe_for_high_impact": bool(region.get("safe_for_high_impact", False)),
        "safe_for_identity_match": bool(region.get("safe_for_identity_match", False)),
        "conflict_sources": list(region.get("conflict_sources") or []),
    }


def _evidence_region_for_row(
    row: dict[str, Any], metadata: dict[str, Any]
) -> dict[str, Any] | None:
    """Read OCR provenance for Evidence without changing the V3 chunk schema."""
    regions = database.get_document_ocr_regions(str(row.get("file_id") or ""))
    region_id = metadata.get("region_id")
    if region_id:
        return next(
            (region for region in regions if region.get("region_id") == region_id),
            None,
        )
    bbox = metadata.get("bbox") or (metadata.get("block") or {}).get("bbox")
    page_no = row.get("page_no")
    if bbox is None:
        return None
    return next(
        (
            region for region in regions
            if region.get("page_no") == page_no and region.get("bbox") == bbox
        ),
        None,
    )


def _merge_retried_regions(
    pages: list[dict[str, Any]], replacements: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for original in pages:
        page = dict(original)
        regions = [
            dict(replacements.get(str(region.get("region_id"))) or region)
            for region in page.get("blocks") or []
        ]
        successful = [
            region for region in regions
            if region.get("status") == "success" and str(region.get("text") or "").strip()
        ]
        page["blocks"] = regions
        if page.get("source_type") == "ocr":
            page["text"] = "\n".join(str(region["text"]) for region in successful)
            page["bbox"] = _union_bbox(successful)
            confidences = [
                float(region["confidence"])
                for region in successful if region.get("confidence") is not None
            ]
            page["confidence"] = min(confidences) if confidences else None
            page["status"] = "success" if successful else "failed"
            page["error"] = None if successful else "OCR_REGION_NO_USABLE_TEXT"
            page["updated_at"] = database.utc_now()
        merged.append(page)
    return merged


def _ocr_pages_to_index(
    file_id: str, pages: list[dict[str, Any]]
) -> tuple[list[DocumentBlock], list[dict[str, Any]]]:
    """Rebuild one candidate from the existing page ledger after local retry."""
    blocks: list[DocumentBlock] = []
    chunks: list[dict[str, Any]] = []
    for page in pages:
        if page.get("status") != "success":
            continue
        raw_regions = [
            region for region in page.get("blocks") or []
            if region.get("status") == "success" and str(region.get("text") or "").strip()
        ]
        if not raw_regions and str(page.get("text") or "").strip():
            raw_regions = [{
                "text": page["text"],
                "bbox": page.get("bbox"),
                "confidence": page.get("confidence"),
                "source_parser": "pypdf" if page.get("source_type") == "text" else "rapidocr",
            }]
        for region in raw_regions:
            block = make_document_block(
                file_id=file_id,
                sequence=len(blocks),
                block_type="paragraph",
                content=str(region["text"]),
                page_no=int(page["page_no"]),
                bbox=region.get("bbox") if page.get("source_type") == "ocr" else None,
                confidence=(
                    region.get("confidence") if page.get("source_type") == "ocr" else None
                ),
                source_parser=str(region.get("source_parser") or (
                    "rapidocr" if page.get("source_type") == "ocr" else "pypdf"
                )),
            )
            blocks.append(block)
            generated = blocks_to_chunks(
                [block],
                source_type="ocr" if page.get("source_type") == "ocr" else "pdf",
                metadata_by_block={block.block_id: _region_safety_metadata(region)},
            )
            for chunk in generated:
                chunk["chunk_index"] = len(chunks)
                chunks.append(chunk)
    return blocks, chunks


def _union_bbox(blocks: list[dict[str, Any]]) -> list[float] | None:
    boxes = [block.get("bbox") for block in blocks if block.get("bbox")]
    if not boxes:
        return None
    return [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]


def _resume_failed_index(record: dict[str, Any]) -> dict[str, Any] | None:
    lifecycle = get_file_lifecycle(record["file_id"])
    if not lifecycle["ok"]:
        return lifecycle
    if lifecycle["data"]["status"] != FileLifecycleStatus.FAILED.value:
        return None
    resumed = resume_file_lifecycle(record["file_id"])
    return None if resumed["ok"] else resumed


def _word_chunks(file_id: str, path: Path) -> list[dict[str, Any]]:
    document = Document(path)
    blocks: list[DocumentBlock] = []
    metadata_by_block: dict[str, dict[str, Any]] = {}
    current_section: str | None = None
    current_section_id: str | None = None

    seen_header_footer: set[tuple[str, str]] = set()
    for section in document.sections:
        for block_type, container in (("header", section.header), ("footer", section.footer)):
            text = "\n".join(
                paragraph.text.strip() for paragraph in container.paragraphs
                if paragraph.text.strip()
            )
            key = (block_type, text)
            if not text or key in seen_header_footer:
                continue
            seen_header_footer.add(key)
            block = make_document_block(
                file_id=file_id, sequence=len(blocks), block_type=block_type,
                content=text, source_parser="python-docx",
            )
            blocks.append(block)
            metadata_by_block[block.block_id] = {"word_region": block_type}

    for paragraph_no, paragraph in enumerate(document.paragraphs, start=1):
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = str(paragraph.style.name or "")
        is_heading = style_name.casefold().startswith("heading") or style_name.startswith("标题")
        if is_heading:
            current_section = text
        block = make_document_block(
            file_id=file_id,
            sequence=len(blocks),
            block_type="title" if is_heading else "paragraph",
            content=text,
            parent_id=None if is_heading else current_section_id,
            source_parser="python-docx",
        )
        blocks.append(block)
        metadata_by_block[block.block_id] = {
                "paragraph_no": paragraph_no,
                "heading": text if is_heading else None,
                "section": current_section,
                "paragraph_start": paragraph_no,
                "paragraph_end": paragraph_no,
        }
        if is_heading:
            current_section_id = block.block_id

    for image_no, _shape in enumerate(document.inline_shapes, start=1):
        image_block = make_document_block(
            file_id=file_id, sequence=len(blocks), block_type="image", content="",
            parent_id=current_section_id, source_parser="python-docx",
            status="degraded", warnings=["IMAGE_TEXT_NOT_EXTRACTED"],
        )
        blocks.append(image_block)
        metadata_by_block[image_block.block_id] = {"image_no": image_no}

    for table_no, table in enumerate(document.tables, start=1):
        rows = [
            [cell.text.strip() for cell in row.cells]
            for row in table.rows
        ]
        table_content = "\n".join("\t".join(row) for row in rows)
        table_block = make_document_block(
            file_id=file_id,
            sequence=len(blocks),
            block_type="table",
            content=table_content,
            parent_id=current_section_id,
            source_parser="python-docx",
        )
        blocks.append(table_block)
        if not _is_simple_word_table(table, rows):
            structure = degraded_table(
                table_id=table_block.block_id, file_id=file_id,
                raw_text=table_content, source_parser="python-docx",
                warning="MERGED_OR_IRREGULAR_CELLS_UNSUPPORTED",
            )
            table_block.status = "degraded"
            table_block.warnings = list(structure.warnings)
            metadata_by_block[table_block.block_id] = {
                "table_no": table_no,
                "table_id": table_block.block_id,
                "table_structure": structure.model_dump(),
                "layout_status": "degraded",
                "layout_warnings": list(structure.warnings),
            }
            continue

        structure = build_simple_table(
            table_id=table_block.block_id, file_id=file_id, rows=rows,
            page_no=None, source_parser="python-docx",
        )
        metadata_by_block[table_block.block_id] = {
            "table_no": table_no,
            "table_id": table_block.block_id,
            "table_structure": structure.model_dump(),
        }
        for cell in structure.cells:
            cell_block = make_document_block(
                file_id=file_id,
                sequence=len(blocks),
                block_type="cell",
                content=cell.cell_text,
                parent_id=table_block.block_id,
                source_parser="python-docx",
                block_id=cell.cell_id,
            )
            blocks.append(cell_block)
            metadata_by_block[cell_block.block_id] = {
                "table_no": table_no,
                "table_id": table_block.block_id,
                "cell_id": cell.cell_id,
                "row_no": cell.row_index + 1,
                "column_no": cell.column_index + 1,
                "row_index": cell.row_index,
                "column_index": cell.column_index,
                "bbox": cell.bbox,
                "confidence": cell.confidence,
            }

    return blocks_to_chunks(
        blocks,
        source_type="word",
        metadata_by_block=metadata_by_block,
    )


def _is_simple_word_table(table: Any, rows: list[list[str]]) -> bool:
    """Accept only a rectangular grid with one distinct XML cell per coordinate."""
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        return False
    xml_cells = [cell._tc for row in table.rows for cell in row.cells]
    return len({id(cell) for cell in xml_cells}) == len(xml_cells)


def _split_long_text(text: str) -> list[str]:
    step = WORD_CHUNK_SIZE - WORD_CHUNK_OVERLAP
    return [text[start : start + WORD_CHUNK_SIZE] for start in range(0, len(text), step)]


def _chunk(
    *, file_id: str, chunk_index: int, text: str, page_no: int | None, metadata: dict[str, Any]
) -> dict[str, Any]:
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    chunk_id = hashlib.sha256(
        f"{file_id}:{chunk_index}:{text_hash}".encode("utf-8")
    ).hexdigest()[:32]
    return {
        "chunk_id": chunk_id,
        "file_id": file_id,
        "page_no": page_no,
        "chunk_index": chunk_index,
        "chunk_text": text,
        "text_hash": text_hash,
        "metadata": metadata,
    }


def _persist_index(
    record: dict[str, Any],
    chunks: list[dict[str, Any]],
    page_count: int | None = None,
    extra_data: dict[str, Any] | None = None,
    ocr_pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    index_started = time.perf_counter()
    validation_error = _validate_candidate_chunks(record["file_id"], chunks)
    if validation_error is not None:
        _record_document_stage(
            record, "index", index_started, "failed",
            metrics={"candidate_count": len(chunks)},
            error_code="INDEX_VALIDATION_ERROR",
        )
        return _index_failure(
            record,
            "INDEX_VALIDATION_ERROR",
            validation_error,
            failure_stage="index",
        )
    try:
        from_status = database.activate_document_index(
            record["file_id"], chunks, ocr_pages=ocr_pages
        )
        record_file_lifecycle_transition(
            file_id=record["file_id"],
            from_status=from_status,
            to_status=FileLifecycleStatus.QUERYABLE,
        )
    except Exception:
        _record_document_stage(
            record, "index", index_started, "failed",
            metrics={"candidate_count": len(chunks)}, error_code="INDEX_WRITE_ERROR",
        )
        return _index_failure(
            record,
            "INDEX_WRITE_ERROR",
            "文档索引保存失败",
            failure_stage="index",
        )
    data = {
            "status": "indexed",
            "file_id": record["file_id"],
            "file_name": record["file_name"],
            "page_count": page_count,
            "chunk_count": len(chunks),
        }
    data.update(extra_data or {})
    _record_document_stage(
        record, "index", index_started, "success",
        metrics={"candidate_count": len(chunks), "active_chunk_count": len(chunks)},
    )
    return success(data, "文档索引建立成功")


def _record_document_stage(
    record: dict[str, Any],
    stage: str,
    started: float,
    status: str,
    *,
    metrics: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> None:
    """Persist stage observability without making Trace authoritative."""
    try:
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        provided = dict(metrics or {})
        page_count = provided.get("page_count", provided.get("total_pages"))
        success_count = provided.get(
            "successful_regions", provided.get("successful_pages")
        )
        failure_count = provided.get("failed_regions", provided.get("failed_pages"))
        standardized = {
            "latency_ms": duration_ms,
            "page_count": page_count,
            "image_count": provided.get("image_count"),
            "region_count": provided.get("region_count"),
            "ocr_success_count": success_count,
            "ocr_failure_count": failure_count,
            "ocr_call_count": 1 if stage in {"ocr", "visual_ocr"} else 0,
            "vision_call_count": 1 if stage in {"detect", "visual_ocr", "layout"} else 0,
            "visual_processing_duration_ms": (
                duration_ms if stage in {"detect", "ocr", "visual_ocr", "layout"}
                else None
            ),
        }
        record_trace(
            session_id=f"document:{record['file_id']}",
            event_type="document_stage",
            tool_name=f"{stage}_document",
            arguments={"file_id": record["file_id"], "stage": stage},
            result={"status": status, "file_id": record["file_id"]},
            duration_ms=duration_ms,
            result_status=status,
            error_code=error_code,
            metrics={
                "stage": stage,
                **{key: value for key, value in standardized.items() if value is not None},
                **provided,
            },
        )
    except Exception:
        pass


def _visual_region_trace_metrics(
    regions: list[dict[str, Any]],
) -> dict[str, Any]:
    handwriting_confidences = [
        float(region["confidence"])
        for region in regions
        if region.get("recognition_type") in {"handwritten", "mixed"}
        and isinstance(region.get("confidence"), (int, float))
    ]
    return {
        "region_count": len(regions),
        "successful_regions": sum(
            region.get("status") == "success" for region in regions
        ),
        "failed_regions": sum(
            region.get("status") in {"low_confidence", "empty", "failed"}
            for region in regions
        ),
        "handwriting_region_count": sum(
            region.get("recognition_type") in {"handwritten", "mixed"}
            for region in regions
        ),
        "handwriting_confidence": (
            min(handwriting_confidences) if handwriting_confidences else None
        ),
        "handwriting_confidence_average": (
            round(sum(handwriting_confidences) / len(handwriting_confidences), 6)
            if handwriting_confidences else None
        ),
    }


def _mark_index_pending(record: dict[str, Any]) -> dict[str, Any] | None:
    target = (
        FileLifecycleStatus.REINDEXING
        if _has_active_index(record["file_id"])
        else FileLifecycleStatus.INDEXING
    )
    try:
        transition = transition_file_lifecycle(
            record["file_id"], target
        )
        updated = transition["ok"]
    except Exception:
        updated = False
    if not updated:
        return failure(
            "INDEX_STATE_ERROR",
            "无法将文件切换到待索引状态",
            {"file_id": record["file_id"]},
        )
    return None


def _index_failure(
    record: dict[str, Any],
    error_code: str,
    message: str,
    *,
    failure_stage: str,
) -> dict[str, Any]:
    preserved_active_index = _has_active_index(record["file_id"])
    try:
        target = (
            FileLifecycleStatus.QUERYABLE
            if preserved_active_index
            else FileLifecycleStatus.FAILED
        )
        transition_file_lifecycle(
            record["file_id"],
            target,
            error_code=error_code,
            error_message=message,
            failure_stage=failure_stage,
        )
    except Exception:
        pass
    return failure(
        error_code,
        message,
        {
            "file_id": record["file_id"],
            "failure_stage": failure_stage,
            "active_index_preserved": preserved_active_index,
        },
    )


def _begin_candidate_build(
    record: dict[str, Any], *, reprocess: bool
) -> dict[str, Any] | None:
    """Expose a rebuild state while leaving the current active rows queryable."""
    if not _has_active_index(record["file_id"]):
        return None
    target = (
        FileLifecycleStatus.REPROCESSING
        if reprocess
        else FileLifecycleStatus.REINDEXING
    )
    started = transition_file_lifecycle(record["file_id"], target)
    if started["ok"]:
        return None
    return failure(
        "INDEX_STATE_ERROR",
        "无法开始文档重处理" if reprocess else "无法开始文档重新索引",
        {"file_id": record["file_id"]},
    )


def _has_active_index(file_id: str) -> bool:
    current = database.get_file_record_by_id(file_id)
    if current is None:
        return False
    return (
        bool(current.get("queryable"))
        and current.get("lifecycle_status") == "ready"
        and current.get("index_status") == "indexed"
        and bool(database.get_document_chunks(file_id))
    )


def _validate_candidate_chunks(file_id: str, chunks: list[dict[str, Any]]) -> str | None:
    """Validate a complete in-memory candidate before mutating active rows."""
    if not chunks:
        return "候选索引没有可激活的 chunk"
    chunk_ids: set[str] = set()
    chunk_indexes: list[int] = []
    for chunk in chunks:
        if chunk.get("file_id") != file_id:
            return "候选索引包含其他文件的 chunk"
        chunk_id = str(chunk.get("chunk_id") or "")
        if not chunk_id or chunk_id in chunk_ids:
            return "候选索引的 chunk_id 缺失或重复"
        chunk_ids.add(chunk_id)
        try:
            chunk_index = int(chunk["chunk_index"])
        except (KeyError, TypeError, ValueError):
            return "候选索引的 chunk_index 无效"
        chunk_indexes.append(chunk_index)
        text = chunk.get("chunk_text")
        if not isinstance(text, str):
            return "候选索引的 chunk_text 无效"
        expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if chunk.get("text_hash") != expected_hash:
            return "候选索引的 text_hash 校验失败"
    if sorted(chunk_indexes) != list(range(len(chunks))):
        return "候选索引的 chunk_index 必须连续且唯一"
    return None


def _validated_document(
    file_id: str, expected_type: str | None = None
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    try:
        arguments = FileIdArguments.model_validate({"file_id": file_id})
    except ValidationError as exc:
        return "", None, failure("INVALID_FILE_ID", _validation_message(exc))
    record = database.get_file_record_by_id(arguments.file_id)
    if record is None or record["lifecycle_status"] == "deleted":
        return arguments.file_id, None, failure("FILE_NOT_FOUND", "未找到指定文件")
    if expected_type is not None and record["file_type"] != expected_type:
        return arguments.file_id, record, failure("UNSUPPORTED_FILE_TYPE", f"该接口仅支持 {expected_type}")
    if record["file_type"] not in SUPPORTED_DOCUMENT_TYPES:
        return arguments.file_id, record, failure(
            "UNSUPPORTED_FILE_TYPE", "仅支持 PDF、Word、图片、PPT/PPTX、TXT 和 JSON 文档索引"
        )
    return arguments.file_id, record, None


def _excerpt(text: str, query: str, limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    position = compact.casefold().find(query.strip().casefold())
    start = max(0, position - limit // 3) if position >= 0 else 0
    excerpt = compact[start : start + limit]
    if start:
        excerpt = "…" + excerpt
    if start + limit < len(compact):
        excerpt += "…"
    return excerpt


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors(include_url=False)[0]
    location = ".".join(str(item) for item in first["loc"])
    return f"参数 {location} 无效：{first['msg']}"


class PdfDependencyError(RuntimeError):
    pass


class PdfParseError(RuntimeError):
    pass
