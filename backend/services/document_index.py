"""Local PDF/Word parsing, deterministic chunking, and FTS-backed retrieval."""

import hashlib
import re
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
    transition_file_lifecycle,
)
from backend.services.file_locator import FileLocatorError, resolve_by_file_id
from backend.services.hybrid_retrieval import hybrid_retrieve
from backend.tool_models import FileIdArguments, RetrieveDocumentArguments
from backend.tools.excel_utils import failure, success


WORD_CHUNK_SIZE = 900
WORD_CHUNK_OVERLAP = 100
SUPPORTED_DOCUMENT_TYPES = {"pdf", "word"}
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
    try:
        path = resolve_by_file_id(clean_file_id)
        pages = _load_pdf_pages(path)
    except PdfDependencyError:
        return _index_failure(
            record,
            "PDF_DEPENDENCY_MISSING",
            "缺少 pypdf，无法解析普通文本 PDF",
            failure_stage="parse",
        )
    except FileLocatorError as exc:
        return _index_failure(
            record, "FILE_NOT_FOUND", str(exc), failure_stage="parse"
        )
    except Exception:
        return _index_failure(
            record,
            "PDF_PARSE_ERROR",
            "PDF 文件无法正常解析",
            failure_stage="parse",
        )

    normalized_pages = [(page_no, text.strip()) for page_no, text in pages]
    text_page_count = sum(bool(text) for _, text in normalized_pages)
    if text_page_count != len(normalized_pages):
        pdf_type = "scanned" if text_page_count == 0 else "mixed"
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


def ocr_document(file_id: str, pages: list[int] | None = None) -> dict[str, Any]:
    """Process all OCR-needed pages or retry only an explicit 1-based page set."""
    return parse_pdf(file_id, _ocr_pages=pages)


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
    resume_error = _resume_failed_index(record)
    if resume_error is not None:
        return resume_error
    operation_error = _begin_candidate_build(record, reprocess=reprocess)
    if operation_error is not None:
        return operation_error
    try:
        path = resolve_by_file_id(clean_file_id)
        chunks = _word_chunks(clean_file_id, path)
    except FileLocatorError as exc:
        return _index_failure(
            record, "FILE_NOT_FOUND", str(exc), failure_stage="parse"
        )
    except Exception:
        return _index_failure(
            record,
            "WORD_PARSE_ERROR",
            "Word 文件无法正常解析",
            failure_stage="parse",
        )
    if not chunks:
        return _index_failure(
            record,
            "NO_TEXT_CONTENT",
            "Word 文档中没有可索引文本",
            failure_stage="parse",
        )
    pending_error = _mark_index_pending(record)
    if pending_error is not None:
        return pending_error
    blocks = blocks_from_chunks(chunks)
    return _persist_index(
        record,
        chunks,
        extra_data={
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
    if arguments.scope.page is not None:
        failed_page_files = [
            file_id for file_id in eligible
            if (
                (database.get_document_ocr_page(file_id, arguments.scope.page) or {}).get("status")
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
                    "failed_page": arguments.scope.page,
                    "failed_page_files": failed_page_files,
                    "result_summary": message,
                },
                message,
            )
            result.update({"evidence": [], "warnings": ["OCR_FAILED_PAGE"], "result_summary": message})
            return result
    retrieval = hybrid_retrieve(
        file_ids=list(eligible),
        query=arguments.query,
        limit=arguments.top_k,
        page_no=arguments.scope.page,
    )
    rows = retrieval["rows"]
    evidence = []
    for row in rows:
        record = eligible[row["file_id"]]
        metadata = row.get("metadata") or {}
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
                chunk_id=row["chunk_id"],
                block_id=block_id,
                table=table,
                cell=cell,
                bbox=metadata.get("bbox"),
                confidence=metadata.get("confidence"),
                field=None,
                record_key=None,
                value_summary=_excerpt(row["chunk_text"], arguments.query),
            )
        )
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
            "result_summary": f"找到 {len(evidence)} 条材料依据",
        },
        "文档检索完成",
    )
    warnings = [retrieval["warning"]] if retrieval["warning"] else []
    if low_confidence_pages:
        warnings.append("LOW_OCR_CONFIDENCE_REVIEW_REQUIRED")
    result.update(
        {
            "evidence": evidence,
            "warnings": warnings,
            "low_confidence_pages": low_confidence_pages,
            "result_summary": f"找到 {len(evidence)} 条材料依据",
        }
    )
    return result


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
    try:
        pages = _load_pdf_pages(path)
    except PdfDependencyError:
        return failure("PDF_DEPENDENCY_MISSING", "缺少 pypdf，无法解析普通文本 PDF")
    except Exception:
        return failure("INVALID_FILE_CONTENT", "文件不是可正常打开的 PDF 文档")
    return None


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
        recognized = (
            ocr_service.ocr_pdf(path)
            if pdf_type == "scanned" and requested_pages is None
            else ocr_service.ocr_document(path, pages=selected)
        )
        recognized_pages = _recognized_page_results(
            recognized, selected, existing=existing
        )
        candidate_pages.update({page["page_no"]: page for page in recognized_pages})

    ordered_pages = [candidate_pages[page_no] for page_no in sorted(candidate_pages)]
    failed_pages = [page["page_no"] for page in ordered_pages if page["status"] == "failed"]
    successful_pages = [page for page in ordered_pages if page["status"] == "success" and page["text"].strip()]
    if not successful_pages:
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
        raw_blocks = page.get("blocks") or [{
            "page": page["page_no"], "text": page["text"],
            "confidence": page.get("confidence"), "bbox": page.get("bbox"),
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
                source_parser=(
                    "rapidocr" if page["source_type"] == "ocr" else "pypdf"
                ),
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
            "ocr_status": "partial_success" if failed_pages else "success",
            "status": "partial_success" if failed_pages else "indexed",
            "pdf_type": pdf_type,
            "failed_pages": failed_pages,
            "low_confidence_pages": low_confidence_pages,
            "verification_required": bool(low_confidence_pages),
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
) -> list[dict[str, Any]]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    supplied = data.get("pages") or []
    by_page = {int(page["page_no"]): dict(page) for page in supplied if page.get("page_no")}
    if not supplied:
        grouped: dict[int, list[dict[str, Any]]] = {}
        for block in data.get("blocks") or []:
            grouped.setdefault(int(block["page"]), []).append(block)
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
        if page.get("status") == "failed" and existing.get(page_no, {}).get("status") == "success":
            page = existing[page_no]
        page = {**page, "page_no": page_no, "source_type": "ocr", "updated_at": database.utc_now()}
        normalized.append(page)
    return normalized


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
    validation_error = _validate_candidate_chunks(record["file_id"], chunks)
    if validation_error is not None:
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
    return success(data, "文档索引建立成功")


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
        return arguments.file_id, record, failure("UNSUPPORTED_FILE_TYPE", "仅支持 PDF 和 Word 文档索引")
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
