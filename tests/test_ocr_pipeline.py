"""V3.4 normal PDF, scanned PDF and OCR failure tests."""

import json
from pathlib import Path

import pytest

from backend import database
from backend.services import document_index, ocr_service
from backend.services.document_index import parse_pdf, validate_pdf_file
from backend.services.file_lifecycle import TRACE_SESSION_PREFIX, get_file_lifecycle
from backend.tools import excel_utils


@pytest.fixture
def pdf_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    path = upload_dir / "材料.pdf"
    path.write_bytes(b"%PDF deterministic test fixture")
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    database.register_file(
        file_name=path.name,
        file_type="pdf",
        file_path=f"data/uploads/{path.name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
    )
    record = database.get_file_record(path.name)
    assert record is not None
    return record


def test_normal_pdf_keeps_embedded_text_pipeline(
    pdf_record: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        document_index,
        "_load_pdf_pages",
        lambda path: [(1, "普通 PDF 的内嵌文本。"), (2, "第二页内容。")],
    )

    def reject_ocr(path: Path):
        raise AssertionError("普通 PDF 不应调用 OCR")

    monkeypatch.setattr(document_index.ocr_service, "ocr_pdf", reject_ocr)

    result = parse_pdf(pdf_record["file_id"])

    assert result["ok"] is True
    assert result["data"]["ocr_used"] is False
    assert result["data"]["page_count"] == 2
    chunks = database.get_document_chunks(pdf_record["file_id"])
    assert [chunk["chunk_text"] for chunk in chunks] == [
        "普通 PDF 的内嵌文本。",
        "第二页内容。",
    ]
    assert all(chunk["metadata"]["source_type"] == "pdf" for chunk in chunks)


def test_ocr_service_returns_page_text_confidence_and_bbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DeterministicEngine:
        def __call__(self, image):
            return (
                [
                    [
                        [[10, 20], [80, 20], [80, 45], [10, 45]],
                        "真实引擎返回文本",
                        0.94,
                    ]
                ],
                0.01,
            )

    monkeypatch.setattr(ocr_service, "_render_pdf_pages", lambda path: [(3, object())])
    monkeypatch.setattr(ocr_service, "_create_engine", DeterministicEngine)

    result = ocr_service.ocr_pdf(Path("adapter-test.pdf"))

    assert result["ok"] is True
    assert result["data"]["blocks"] == [
        {
            "page": 3,
            "text": "真实引擎返回文本",
            "confidence": 0.94,
            "bbox": [10.0, 20.0, 80.0, 45.0],
        }
    ]


def test_scanned_pdf_enters_ocr_and_preserves_real_block_metadata(
    pdf_record: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        document_index, "_load_pdf_pages", lambda path: [(1, ""), (2, "   ")]
    )
    blocks = [
        {
            "page": 1,
            "text": "扫描识别第一页",
            "confidence": 0.96,
            "bbox": [10.0, 20.0, 180.0, 48.0],
        },
        {
            "page": 2,
            "text": "扫描识别第二页",
            "confidence": 0.91,
            "bbox": [12.0, 22.0, 190.0, 52.0],
        },
    ]
    monkeypatch.setattr(
        document_index.ocr_service,
        "ocr_pdf",
        lambda path: {
            "ok": True,
            "data": {"blocks": blocks, "page_count": 2},
            "error_code": None,
            "message": "OCR 完成",
        },
    )

    assert validate_pdf_file(Path("ignored-by-adapter.pdf")) is None
    result = parse_pdf(pdf_record["file_id"])

    assert result["ok"] is True
    assert result["data"]["ocr_used"] is True
    assert result["data"]["ocr_results"] == blocks
    chunks = database.get_document_chunks(pdf_record["file_id"])
    assert [chunk["page_no"] for chunk in chunks] == [1, 2]
    assert [chunk["metadata"] for chunk in chunks] == [
        {
            "source_type": "ocr",
            "page_no": block["page"],
            "confidence": block["confidence"],
            "bbox": block["bbox"],
        }
        for block in blocks
    ]
    traces = database.get_session_trace_records(
        f"{TRACE_SESSION_PREFIX}{pdf_record['file_id']}"
    )
    assert [json.loads(trace["arguments_summary"])["to_status"] for trace in traces] == [
        "OCR_PROCESSING",
        "INDEXING",
        "QUERYABLE",
    ]


def test_ocr_failure_records_reason_and_never_creates_chunks(
    pdf_record: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(document_index, "_load_pdf_pages", lambda path: [(1, "")])
    monkeypatch.setattr(
        document_index.ocr_service,
        "ocr_pdf",
        lambda path: {
            "ok": False,
            "data": None,
            "error_code": "OCR_ENGINE_ERROR",
            "message": "OCR 引擎执行失败",
        },
    )

    result = parse_pdf(pdf_record["file_id"])

    assert result["ok"] is False
    assert result["error_code"] == "OCR_ENGINE_ERROR"
    assert database.get_document_chunks(pdf_record["file_id"]) == []
    lifecycle = get_file_lifecycle(pdf_record["file_id"])["data"]
    assert lifecycle["status"] == "FAILED"
    assert lifecycle["error"]["error_code"] == "OCR_ENGINE_ERROR"
    assert lifecycle["error"]["message"] == "OCR 引擎执行失败"
    assert lifecycle["error"]["resume_status"] == "OCR_PROCESSING"

    recovered_blocks = [
        {
            "page": 1,
            "text": "恢复后识别文本",
            "confidence": 0.9,
            "bbox": [1.0, 2.0, 30.0, 12.0],
        }
    ]
    monkeypatch.setattr(
        document_index.ocr_service,
        "ocr_pdf",
        lambda path: {
            "ok": True,
            "data": {"blocks": recovered_blocks, "page_count": 1},
            "error_code": None,
            "message": "OCR 恢复",
        },
    )

    retried = parse_pdf(pdf_record["file_id"])

    assert retried["ok"] is True
    assert retried["data"]["ocr_results"] == recovered_blocks
    assert get_file_lifecycle(pdf_record["file_id"])["data"]["status"] == "QUERYABLE"
