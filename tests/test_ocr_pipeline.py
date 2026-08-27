"""V3.4 normal PDF, scanned PDF and OCR failure tests."""

import io
import json
from pathlib import Path

import pytest

from backend import database
from backend.services import document_index, ocr_service
from backend.services.document_index import parse_pdf, retrieve_document, validate_pdf_file
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
    document_blocks = result["data"]["blocks"]
    assert [chunk["metadata"] for chunk in chunks] == [
        {
            "source_type": "ocr",
            "block_id": document_block["block_id"],
            "block_type": "paragraph",
            "block": document_block,
            "page_no": block["page"],
            "confidence": block["confidence"],
            "bbox": block["bbox"],
        }
        for block, document_block in zip(blocks, document_blocks)
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


@pytest.mark.parametrize(
    ("texts", "expected"),
    [(["正文", "第二页"], "text"), (["", "  "], "scanned"), (["正文", ""], "mixed")],
    ids=["O01-text", "O01-scanned", "O04-mixed"],
)
def test_o01_o04_detect_pdf_type_per_page(
    texts: list[str], expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ocr_service,
        "detect_pdf_text",
        lambda path: {
            "ok": True,
            "data": {
                "has_text": any(text.strip() for text in texts),
                "page_count": len(texts),
                "pages": [
                    {"page": page_no, "text": text}
                    for page_no, text in enumerate(texts, start=1)
                ],
            },
            "error_code": None,
            "message": "detected",
        },
    )
    result = ocr_service.detect_pdf_type(Path("type.pdf"))
    assert result["data"]["pdf_type"] == expected
    assert result["data"]["needs_ocr_pages"] == [
        index for index, text in enumerate(texts, start=1) if not text.strip()
    ]


def test_o04_mixed_pdf_only_ocrs_pages_without_text(
    pdf_record: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        document_index,
        "_load_pdf_pages",
        lambda path: [(1, "文本层第一页"), (2, ""), (3, "文本层第三页")],
    )
    called_pages: list[list[int]] = []

    def selected_ocr(path: Path, pages: list[int]):
        called_pages.append(pages)
        block = {"page": 2, "text": "OCR 第二页", "confidence": 0.93,
                 "bbox": [1.0, 2.0, 30.0, 12.0]}
        return {
            "ok": True,
            "data": {
                "status": "success", "blocks": [block], "failed_pages": [],
                "page_count": 1,
                "pages": [{
                    "page_no": 2, "text": block["text"], "bbox": block["bbox"],
                    "confidence": block["confidence"], "status": "success",
                    "error": None, "source_type": "ocr", "blocks": [block],
                }],
            },
            "error_code": None,
            "message": "OCR success",
        }

    monkeypatch.setattr(document_index.ocr_service, "ocr_document", selected_ocr)
    result = parse_pdf(pdf_record["file_id"])
    assert result["ok"] is True
    assert result["data"]["pdf_type"] == "mixed"
    assert called_pages == [[2]]
    assert [page["source_type"] for page in database.get_document_ocr_pages(pdf_record["file_id"])] == [
        "text", "ocr", "text"
    ]
    assert [chunk["page_no"] for chunk in database.get_document_chunks(pdf_record["file_id"])] == [1, 2, 3]


def test_o05_o07_page_ledger_partial_success_and_failed_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ocr_service, "_render_pdf_pages",
        lambda path: [(page_no, page_no) for page_no in range(1, 21)],
    )

    class PartialEngine:
        def __call__(self, image):
            if image == 20:
                raise RuntimeError("page backend failure")
            return ([[[[1, 2], [9, 2], [9, 6], [1, 6]], f"page {image}", 0.88]], 0.01)

    monkeypatch.setattr(ocr_service, "_create_engine", PartialEngine)
    result = ocr_service.ocr_document(Path("partial.pdf"))
    assert result["ok"] is True
    assert result["data"]["status"] == "partial_success"
    assert result["data"]["failed_pages"] == [20]
    assert len([page for page in result["data"]["pages"] if page["status"] == "success"]) == 19
    failed = result["data"]["pages"][-1]
    assert set(failed) >= {"page_no", "text", "bbox", "confidence", "status", "error"}
    assert failed["error"] == "OCR 识别过程失败"


def test_o08_retry_only_failed_page_preserves_successful_pages(
    pdf_record: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(document_index, "_load_pdf_pages", lambda path: [(1, ""), (2, ""), (3, "")])

    def page(page_no: int, text: str, confidence: float = 0.9) -> dict:
        block = {"page": page_no, "text": text, "confidence": confidence,
                 "bbox": [1.0, 2.0, 20.0, 8.0]}
        return {"page_no": page_no, "text": text, "bbox": block["bbox"],
                "confidence": confidence, "status": "success", "error": None,
                "source_type": "ocr", "blocks": [block]}

    first_pages = [page(1, "成功一"), {
        "page_no": 2, "text": "", "bbox": None, "confidence": None,
        "status": "failed", "error": "page failed", "source_type": "ocr", "blocks": [],
    }, page(3, "成功三")]
    monkeypatch.setattr(document_index.ocr_service, "ocr_pdf", lambda path: {
        "ok": True, "data": {"status": "partial_success", "pages": first_pages,
        "blocks": first_pages[0]["blocks"] + first_pages[2]["blocks"],
        "failed_pages": [2], "page_count": 3}, "error_code": None, "message": "partial",
    })
    first = parse_pdf(pdf_record["file_id"])
    assert first["data"]["status"] == "partial_success"
    assert first["data"]["failed_pages"] == [2]

    retried_pages: list[list[int]] = []

    def retry(path: Path, pages: list[int]):
        retried_pages.append(pages)
        recovered = page(2, "恢复二")
        return {"ok": True, "data": {"status": "success", "pages": [recovered],
                "blocks": recovered["blocks"], "failed_pages": [], "page_count": 1},
                "error_code": None, "message": "recovered"}

    monkeypatch.setattr(document_index.ocr_service, "ocr_document", retry)
    recovered = document_index.ocr_document(pdf_record["file_id"], pages=[2])
    assert recovered["ok"] is True
    assert recovered["data"]["failed_pages"] == []
    assert retried_pages == [[2]]
    assert [page_result["text"] for page_result in database.get_document_ocr_pages(pdf_record["file_id"])] == [
        "成功一", "恢复二", "成功三"
    ]


def test_o09_o10_low_confidence_evidence_warns_and_failed_page_is_insufficient(
    pdf_record: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(document_index, "_load_pdf_pages", lambda path: [(1, ""), (2, "")])
    block = {"page": 1, "text": "低置信度关键字段 奖学金", "confidence": 0.55,
             "bbox": [2.0, 3.0, 40.0, 15.0]}
    pages = [{"page_no": 1, "text": block["text"], "bbox": block["bbox"],
              "confidence": 0.55, "status": "success", "error": None,
              "source_type": "ocr", "blocks": [block]},
             {"page_no": 2, "text": "", "bbox": None, "confidence": None,
              "status": "failed", "error": "unreadable", "source_type": "ocr", "blocks": []}]
    monkeypatch.setattr(document_index.ocr_service, "ocr_pdf", lambda path: {
        "ok": True, "data": {"status": "partial_success", "pages": pages,
        "blocks": [block], "failed_pages": [2], "page_count": 2},
        "error_code": None, "message": "partial",
    })
    parsed = parse_pdf(pdf_record["file_id"])
    assert parsed["data"]["verification_required"] is True
    found = retrieve_document({"file_id": pdf_record["file_id"], "page": 1}, "奖学金", 5)
    assert found["data"]["status"] == "found"
    assert found["evidence"][0]["confidence"] == 0.55
    assert "LOW_OCR_CONFIDENCE_REVIEW_REQUIRED" in found["warnings"]
    failed = retrieve_document({"file_id": pdf_record["file_id"], "page": 2}, "关键字段", 5)
    assert failed["data"]["status"] == "insufficient_evidence"
    assert failed["evidence"] == []
    assert failed["warnings"] == ["OCR_FAILED_PAGE"]


def test_o03_real_ocr_backend_smoke_is_optional(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    pytest.importorskip("rapidocr_onnxruntime")
    image_module = pytest.importorskip("PIL.Image")
    draw_module = pytest.importorskip("PIL.ImageDraw")
    image = image_module.new("RGB", (1000, 240), "white")
    draw = draw_module.Draw(image)
    draw.text((60, 80), "OCR SMOKE 123456", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    pdf_path = tmp_path / "ocr-smoke.pdf"
    pdf = fitz.open()
    pdf_page = pdf.new_page(width=1000, height=240)
    pdf_page.insert_image(pdf_page.rect, stream=buffer.getvalue())
    pdf.save(pdf_path)
    pdf.close()
    result = ocr_service.ocr_page(pdf_path, 1)
    if result.get("error_code") == "OCR_DEPENDENCY_MISSING":
        pytest.skip(result["message"])
    assert result["data"]["page_no"] == 1
    assert result["data"]["status"] in {"success", "failed"}
