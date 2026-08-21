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
from backend.services import ocr_service
from backend.services.file_lifecycle import (
    get_file_lifecycle,
    resume_file_lifecycle,
    start_ocr_processing,
    transition_file_lifecycle,
)
from backend.services.file_locator import FileLocatorError, resolve_by_file_id
from backend.tool_models import FileIdArguments, RetrieveDocumentArguments
from backend.tools.excel_utils import failure, success


WORD_CHUNK_SIZE = 900
WORD_CHUNK_OVERLAP = 100
SUPPORTED_DOCUMENT_TYPES = {"pdf", "word"}
NO_EVIDENCE_MESSAGE = "当前材料中未找到足够依据。"
OCR_NOT_SUPPORTED_MESSAGE = "当前版本不支持扫描 PDF / OCR。"


def parse_pdf(file_id: str) -> dict[str, Any]:
    """Index embedded PDF text or route an image-only PDF through local OCR."""
    clean_file_id, record, error = _validated_document(file_id, expected_type="pdf")
    if error is not None:
        return error
    resume_error = _resume_failed_index(record)
    if resume_error is not None:
        return resume_error
    try:
        path = resolve_by_file_id(clean_file_id)
        pages = _load_pdf_pages(path)
    except PdfDependencyError:
        return _index_failure(
            record, "PDF_DEPENDENCY_MISSING", "缺少 pypdf，无法解析普通文本 PDF"
        )
    except FileLocatorError as exc:
        return _index_failure(record, "FILE_NOT_FOUND", str(exc))
    except Exception:
        return _index_failure(record, "PDF_PARSE_ERROR", "PDF 文件无法正常解析")

    normalized_pages = [(page_no, text.strip()) for page_no, text in pages]
    if not any(text for _, text in normalized_pages):
        return _parse_scanned_pdf(record, path, page_count=len(pages))
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
            "block_count": len(blocks),
            "blocks": [block.model_dump() for block in blocks],
        },
    )


def index_document(file_id: str) -> dict[str, Any]:
    """Build or replace the local index for one registered PDF or Word."""
    clean_file_id, record, error = _validated_document(file_id)
    if error is not None:
        return error
    if record["file_type"] == "pdf":
        return parse_pdf(clean_file_id)
    resume_error = _resume_failed_index(record)
    if resume_error is not None:
        return resume_error
    pending_error = _mark_index_pending(record)
    if pending_error is not None:
        return pending_error
    try:
        path = resolve_by_file_id(clean_file_id)
        chunks = _word_chunks(clean_file_id, path)
    except FileLocatorError as exc:
        return _index_failure(record, "FILE_NOT_FOUND", str(exc))
    except Exception:
        return _index_failure(record, "WORD_PARSE_ERROR", "Word 文件无法正常解析")
    if not chunks:
        return _index_failure(record, "NO_TEXT_CONTENT", "Word 文档中没有可索引文本")
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
            record["file_type"] in SUPPORTED_DOCUMENT_TYPES
            and record["lifecycle_status"] == "ready"
            and record["index_status"] == "indexed"
            and bool(record["queryable"])
        ):
            eligible[record["file_id"]] = record
    rows = database.search_document_chunks(
        file_ids=list(eligible),
        query=arguments.query,
        limit=arguments.top_k,
    )
    evidence = []
    for row in rows:
        record = eligible[row["file_id"]]
        metadata = row.get("metadata") or {}
        block_id = metadata.get("block_id")
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
                field=None,
                record_key=None,
                value_summary=_excerpt(row["chunk_text"], arguments.query),
            )
        )
        evidence[-1]["score"] = round(1.0 / (1.0 + abs(float(row["rank"]))), 6)
        evidence[-1]["text_excerpt"] = evidence[-1]["value_summary"]
    if not evidence:
        result = success(
            {
                "status": "not_found",
                "query": arguments.query,
                "evidence": [],
                "result_summary": NO_EVIDENCE_MESSAGE,
            },
            NO_EVIDENCE_MESSAGE,
        )
        result.update(
            {"evidence": [], "warnings": [], "result_summary": NO_EVIDENCE_MESSAGE}
        )
        return result
    result = success(
        {
            "status": "found",
            "query": arguments.query,
            "evidence": evidence,
            "result_summary": f"找到 {len(evidence)} 条材料依据",
        },
        "文档检索完成",
    )
    result.update(
        {
            "evidence": evidence,
            "warnings": [],
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
    started = start_ocr_processing(record["file_id"])
    if not started["ok"]:
        return failure("OCR_STATE_ERROR", started["message"], {"file_id": record["file_id"]})
    recognized = ocr_service.ocr_pdf(path)
    if not recognized["ok"]:
        return _index_failure(
            record,
            recognized.get("error_code") or "OCR_PROCESSING_ERROR",
            recognized.get("message") or "OCR 识别失败",
        )
    indexing = transition_file_lifecycle(
        record["file_id"], FileLifecycleStatus.INDEXING
    )
    if not indexing["ok"]:
        return _index_failure(record, "INDEX_STATE_ERROR", indexing["message"])
    ocr_results = recognized["data"]["blocks"]
    blocks = [
        make_document_block(
            file_id=record["file_id"],
            sequence=index,
            block_type="paragraph",
            content=block["text"],
            page_no=block["page"],
            confidence=block["confidence"],
            bbox=block["bbox"],
        )
        for index, block in enumerate(ocr_results)
    ]
    chunks = blocks_to_chunks(blocks, source_type="ocr")
    return _persist_index(
        record,
        chunks,
        page_count=page_count,
        extra_data={
            "ocr_used": True,
            "ocr_results": ocr_results,
            "block_count": len(blocks),
            "blocks": [block.model_dump() for block in blocks],
        },
    )


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
        )
        blocks.append(block)
        metadata_by_block[block.block_id] = {
                "paragraph_no": paragraph_no,
                "heading": text if is_heading else None,
                "section": current_section,
                "paragraph_start": paragraph_no,
                "paragraph_end": paragraph_no,
        }

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
        )
        blocks.append(table_block)
        metadata_by_block[table_block.block_id] = {"table_no": table_no}
        for row_no, row in enumerate(rows, start=1):
            for column_no, content in enumerate(row, start=1):
                if not content:
                    continue
                cell_block = make_document_block(
                    file_id=file_id,
                    sequence=len(blocks),
                    block_type="cell",
                    content=content,
                )
                blocks.append(cell_block)
                metadata_by_block[cell_block.block_id] = {
                    "table_no": table_no,
                    "row_no": row_no,
                    "column_no": column_no,
                }

    return blocks_to_chunks(
        blocks,
        source_type="word",
        metadata_by_block=metadata_by_block,
    )


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
) -> dict[str, Any]:
    try:
        database.replace_document_chunks(record["file_id"], chunks)
        transitioned = transition_file_lifecycle(
            record["file_id"], FileLifecycleStatus.QUERYABLE
        )
        if not transitioned["ok"]:
            raise RuntimeError(transitioned["message"])
    except Exception:
        return _index_failure(record, "INDEX_WRITE_ERROR", "文档索引保存失败")
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
    try:
        transition = transition_file_lifecycle(
            record["file_id"], FileLifecycleStatus.INDEXING
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
    record: dict[str, Any], error_code: str, message: str
) -> dict[str, Any]:
    try:
        database.delete_document_chunks(record["file_id"])
        transition_file_lifecycle(
            record["file_id"],
            FileLifecycleStatus.FAILED,
            error_code=error_code,
            error_message=message,
        )
    except Exception:
        pass
    return failure(error_code, message, {"file_id": record["file_id"]})


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
