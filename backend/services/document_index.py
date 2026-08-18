"""Local PDF/Word parsing, deterministic chunking, and FTS-backed retrieval."""

import hashlib
import re
from pathlib import Path
from typing import Any

from docx import Document
from pydantic import ValidationError

from backend import database
from backend.evidence import build_evidence, make_evidence_id
from backend.services.file_locator import FileLocatorError, resolve_by_file_id
from backend.tool_models import FileIdArguments, RetrieveDocumentArguments
from backend.tools.excel_utils import failure, success


WORD_CHUNK_SIZE = 900
WORD_CHUNK_OVERLAP = 100
SUPPORTED_DOCUMENT_TYPES = {"pdf", "word"}
NO_EVIDENCE_MESSAGE = "当前材料中未找到足够依据。"
OCR_NOT_SUPPORTED_MESSAGE = "当前版本不支持扫描 PDF / OCR。"


def parse_pdf(file_id: str) -> dict[str, Any]:
    """Extract a registered text PDF page by page and persist page-preserving chunks."""
    clean_file_id, record, error = _validated_document(file_id, expected_type="pdf")
    if error is not None:
        return error
    pending_error = _mark_index_pending(record)
    if pending_error is not None:
        return pending_error
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
        return _index_failure(record, "OCR_NOT_SUPPORTED", OCR_NOT_SUPPORTED_MESSAGE)
    chunks = [
        _chunk(
            file_id=clean_file_id,
            chunk_index=index,
            text=text,
            page_no=page_no,
            metadata={"source_type": "pdf", "page_no": page_no},
        )
        for index, (page_no, text) in enumerate(normalized_pages)
    ]
    return _persist_index(record, chunks, page_count=len(pages))


def index_document(file_id: str) -> dict[str, Any]:
    """Build or replace the local index for one registered PDF or Word."""
    clean_file_id, record, error = _validated_document(file_id)
    if error is not None:
        return error
    if record["file_type"] == "pdf":
        return parse_pdf(clean_file_id)
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
    return _persist_index(record, chunks)


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
        evidence.append(
            build_evidence(
                evidence_id=make_evidence_id(
                    chunk_id=row["chunk_id"], query=arguments.query
                ),
                task_id=None,
                source_type="unstructured",
                file_id=row["file_id"],
                file_name=record["file_name"],
                sheet=None,
                page_no=row["page_no"],
                chunk_id=row["chunk_id"],
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
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise PdfDependencyError from exc
    reader = PdfReader(path, strict=False)
    return [
        (page_no, page.extract_text() or "")
        for page_no, page in enumerate(reader.pages, start=1)
    ]


def validate_pdf_file(path: Path) -> dict[str, Any] | None:
    """Validate a temporary upload and explicitly reject image-only PDFs."""
    try:
        pages = _load_pdf_pages(path)
    except PdfDependencyError:
        return failure("PDF_DEPENDENCY_MISSING", "缺少 pypdf，无法解析普通文本 PDF")
    except Exception:
        return failure("INVALID_FILE_CONTENT", "文件不是可正常打开的 PDF 文档")
    if not any(text.strip() for _, text in pages):
        return failure("OCR_NOT_SUPPORTED", OCR_NOT_SUPPORTED_MESSAGE)
    return None


def _word_chunks(file_id: str, path: Path) -> list[dict[str, Any]]:
    document = Document(path)
    blocks = []
    current_section: str | None = None
    for paragraph_no, paragraph in enumerate(document.paragraphs, start=1):
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = str(paragraph.style.name or "")
        is_heading = style_name.casefold().startswith("heading") or style_name.startswith("标题")
        if is_heading:
            current_section = text
        blocks.append(
            {
                "text": text,
                "paragraph_no": paragraph_no,
                "heading": text if is_heading else None,
                "section": current_section,
            }
        )

    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_length = 0

    def flush() -> None:
        nonlocal current, current_length
        if not current:
            return
        text = "\n".join(item["text"] for item in current)
        chunks.append(
            _chunk(
                file_id=file_id,
                chunk_index=len(chunks),
                text=text,
                page_no=None,
                metadata={
                    "source_type": "word",
                    "paragraph_start": current[0]["paragraph_no"],
                    "paragraph_end": current[-1]["paragraph_no"],
                    "heading": next((item["heading"] for item in current if item["heading"]), None),
                    "section": current[-1]["section"],
                },
            )
        )
        current = []
        current_length = 0

    for block in blocks:
        if block["heading"] and current:
            flush()
        if len(block["text"]) > WORD_CHUNK_SIZE:
            flush()
            for part in _split_long_text(block["text"]):
                chunks.append(
                    _chunk(
                        file_id=file_id,
                        chunk_index=len(chunks),
                        text=part,
                        page_no=None,
                        metadata={
                            "source_type": "word",
                            "paragraph_start": block["paragraph_no"],
                            "paragraph_end": block["paragraph_no"],
                            "heading": block["heading"],
                            "section": block["section"],
                        },
                    )
                )
            continue
        added_length = len(block["text"]) + (1 if current else 0)
        if current and current_length + added_length > WORD_CHUNK_SIZE:
            flush()
        current.append(block)
        current_length += added_length
    flush()
    return chunks


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
    record: dict[str, Any], chunks: list[dict[str, Any]], page_count: int | None = None
) -> dict[str, Any]:
    try:
        database.replace_document_chunks(record["file_id"], chunks)
        database.update_file_state(
            file_id=record["file_id"],
            lifecycle_status="ready",
            parse_status="parsed",
            queryable=True,
            index_status="indexed",
        )
    except Exception:
        return _index_failure(record, "INDEX_WRITE_ERROR", "文档索引保存失败")
    return success(
        {
            "status": "indexed",
            "file_id": record["file_id"],
            "file_name": record["file_name"],
            "page_count": page_count,
            "chunk_count": len(chunks),
        },
        "文档索引建立成功",
    )


def _mark_index_pending(record: dict[str, Any]) -> dict[str, Any] | None:
    try:
        updated = database.update_file_state(
            file_id=record["file_id"],
            lifecycle_status=record["lifecycle_status"],
            parse_status=record["parse_status"],
            queryable=False,
            index_status="pending",
        )
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
        database.update_file_state(
            file_id=record["file_id"],
            lifecycle_status="failed" if record["file_type"] == "pdf" else record["lifecycle_status"],
            parse_status="failed" if record["file_type"] == "pdf" else record["parse_status"],
            queryable=False,
            index_status="failed",
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
