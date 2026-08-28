"""V4.2 unified Visual OCR coverage for images, scanned and mixed PDFs."""

from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from backend import database
from backend.runtime.async_task_runtime import (
    enqueue_async_task,
    retry_async_task,
    run_async_task,
)
from backend.services import document_index, ocr_service
from backend.services.document_index import parse_pdf
from backend.services.file_lifecycle import get_file_lifecycle
from backend.services.file_upload import save_uploaded_file
from backend.tools import excel_utils


pytestmark = pytest.mark.anyio
REGION_FIELDS = {
    "file_id",
    "page_no",
    "image_no",
    "region_id",
    "text",
    "bbox",
    "confidence",
    "recognition_type",
    "source_model",
    "source_parser",
    "status",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def visual_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    return tmp_path


def _image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (180, 80), color="white").save(output, format=image_format)
    return output.getvalue()


class PrintedEngine:
    def __call__(self, image: Any):
        return (
            [
                [
                    [[10, 8], [165, 8], [165, 36], [10, 36]],
                    "姓名 Zhang三 A123",
                    0.96,
                ],
                [
                    [[12, 42], [150, 42], [150, 70], [12, 70]],
                    "成绩 98.5 / PASS",
                    0.91,
                ],
            ],
            0.01,
        )


def _ocr_page(
    page_no: int,
    text: str,
    *,
    confidence: float = 0.9,
    bbox: list[float] | None = None,
) -> dict[str, Any]:
    box = bbox or [1.0, 2.0, 100.0, 24.0]
    block = {
        "page": page_no,
        "text": text,
        "bbox": box,
        "confidence": confidence,
    }
    return {
        "page_no": page_no,
        "text": text,
        "bbox": box,
        "confidence": confidence,
        "status": "success",
        "error": None,
        "source_type": "ocr",
        "blocks": [block],
    }


def _ocr_success(*pages: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "status": "success",
            "pages": list(pages),
            "blocks": [block for page in pages for block in page["blocks"]],
            "failed_pages": [],
            "page_count": len(pages),
        },
        "error_code": None,
        "message": "OCR success",
    }


@pytest.mark.parametrize(
    ("file_name", "image_format", "mime_type"),
    [
        ("打印材料.jpg", "JPEG", "image/jpeg"),
        ("打印材料.png", "PNG", "image/png"),
    ],
)
def test_printed_jpg_and_png_use_visual_ocr_regions(
    visual_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_name: str,
    image_format: str,
    mime_type: str,
) -> None:
    monkeypatch.setattr(ocr_service, "_create_engine", PrintedEngine)

    uploaded = save_uploaded_file(
        file_name,
        _image_bytes(image_format),
        declared_mime_type=mime_type,
    )

    assert uploaded["ok"] is True
    file_id = uploaded["data"]["file_id"]
    regions = database.get_document_ocr_regions(file_id)
    assert [region["text"] for region in regions] == [
        "姓名 Zhang三 A123",
        "成绩 98.5 / PASS",
    ]
    assert all(REGION_FIELDS <= set(region) for region in regions)
    assert all(region["file_id"] == file_id for region in regions)
    assert all(region["image_no"] == 1 for region in regions)
    assert all(region["recognition_type"] == "printed" for region in regions)
    assert all(region["source_parser"] == "rapidocr" for region in regions)
    assert [chunk["chunk_text"] for chunk in database.get_document_chunks(file_id)] == [
        "姓名 Zhang三 A123",
        "成绩 98.5 / PASS",
    ]


def test_image_only_pdf_uses_same_region_ledger(
    visual_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = visual_data_dir / "uploads" / "图片型.pdf"
    path.parent.mkdir()
    path.write_bytes(b"%PDF visual fixture")
    database.register_file(
        file_name=path.name,
        file_type="pdf",
        file_path=f"data/uploads/{path.name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
    )
    record = database.get_file_record(path.name)
    monkeypatch.setattr(document_index, "_load_pdf_pages", lambda path: [(1, "")])
    monkeypatch.setattr(
        document_index.ocr_service,
        "ocr_pdf",
        lambda path: _ocr_success(_ocr_page(1, "图片 PDF 识别 2026")),
    )

    result = parse_pdf(record["file_id"])

    assert result["ok"] is True
    assert result["data"]["pdf_type"] == "scanned"
    regions = database.get_document_ocr_regions(record["file_id"])
    assert len(regions) == 1
    assert regions[0]["text"] == "图片 PDF 识别 2026"
    assert regions[0]["bbox"] == [1.0, 2.0, 100.0, 24.0]
    assert regions[0]["confidence"] == 0.9
    assert REGION_FIELDS <= set(regions[0])


def test_mixed_pdf_routes_only_visual_pages_and_prevents_text_ocr_duplicates(
    visual_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = visual_data_dir / "uploads" / "混合.pdf"
    path.parent.mkdir()
    path.write_bytes(b"%PDF mixed fixture")
    database.register_file(
        file_name=path.name,
        file_type="pdf",
        file_path=f"data/uploads/{path.name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
    )
    record = database.get_file_record(path.name)
    monkeypatch.setattr(
        document_index,
        "_load_pdf_pages",
        lambda path: [(1, "文本层唯一内容"), (2, ""), (3, "另一文本页")],
    )
    selected: list[list[int]] = []

    def visual_pages(path: Path, pages: list[int]):
        selected.append(pages)
        return _ocr_success(_ocr_page(2, "视觉页 OCR 内容"))

    monkeypatch.setattr(document_index.ocr_service, "ocr_document", visual_pages)

    result = parse_pdf(record["file_id"])
    chunks = database.get_document_chunks(record["file_id"])

    assert result["ok"] is True
    assert result["data"]["pdf_type"] == "mixed"
    assert selected == [[2]]
    assert [(chunk["page_no"], chunk["chunk_text"]) for chunk in chunks] == [
        (1, "文本层唯一内容"),
        (2, "视觉页 OCR 内容"),
        (3, "另一文本页"),
    ]
    assert sum(chunk["chunk_text"] == "文本层唯一内容" for chunk in chunks) == 1
    regions = database.get_document_ocr_regions(record["file_id"])
    assert [(region["page_no"], region["text"]) for region in regions] == [
        (2, "视觉页 OCR 内容")
    ]


def test_ocr_empty_region_keeps_bbox_confidence_and_never_fills_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "empty.png"
    path.write_bytes(_image_bytes("PNG"))

    class EmptyRegionEngine:
        def __call__(self, image: Any):
            return (
                [[[[4, 5], [50, 5], [50, 20], [4, 20]], "", 0.42]],
                0.01,
            )

    monkeypatch.setattr(ocr_service, "_create_engine", EmptyRegionEngine)
    result = ocr_service.ocr_visual(path, file_id="f" * 32)

    assert result["ok"] is False
    region = result["data"]["regions"][0]
    assert region["status"] == "empty"
    assert region["text"] == ""
    assert region["bbox"] == [4.0, 5.0, 50.0, 20.0]
    assert region["confidence"] == 0.42
    assert REGION_FIELDS <= set(region)


def test_visual_ocr_page_failure_is_partial_and_persists_failed_region_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ocr_service,
        "_render_pdf_pages",
        lambda path: [(1, 1), (2, 2), (3, 3)],
    )

    class PartialEngine:
        def __call__(self, image: int):
            if image == 2:
                raise RuntimeError("simulated page failure")
            return (
                [[[[1, 2], [40, 2], [40, 12], [1, 12]], f"Page {image} 中文 123", 0.88]],
                0.01,
            )

    monkeypatch.setattr(ocr_service, "_create_engine", PartialEngine)
    result = ocr_service.ocr_visual(Path("partial.pdf"), file_id="e" * 32)

    assert result["ok"] is True
    assert result["data"]["status"] == "partial_success"
    assert result["data"]["failed_pages"] == [2]
    failed = result["data"]["pages"][1]
    assert failed["status"] == "failed"
    assert failed["text"] == ""
    assert failed["blocks"][0]["status"] == "failed"
    assert failed["blocks"][0]["text"] == ""
    assert REGION_FIELDS <= set(failed["blocks"][0])


def test_scanned_pdf_reprocess_failure_preserves_old_chunks_and_regions(
    visual_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = visual_data_dir / "uploads" / "旧扫描结果.pdf"
    path.parent.mkdir()
    path.write_bytes(b"%PDF scanned fixture")
    database.register_file(
        file_name=path.name,
        file_type="pdf",
        file_path=f"data/uploads/{path.name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
    )
    record = database.get_file_record(path.name)
    monkeypatch.setattr(document_index, "_load_pdf_pages", lambda path: [(1, "")])
    monkeypatch.setattr(
        document_index.ocr_service,
        "ocr_pdf",
        lambda path: _ocr_success(_ocr_page(1, "旧有效 OCR 结果")),
    )
    first = parse_pdf(record["file_id"])
    assert first["ok"] is True
    old_chunks = database.get_document_chunks(record["file_id"])
    old_regions = database.get_document_ocr_regions(record["file_id"])

    monkeypatch.setattr(
        document_index.ocr_service,
        "ocr_pdf",
        lambda path: {
            "ok": False,
            "data": {
                "status": "failed",
                "pages": [{
                    "page_no": 1,
                    "text": "",
                    "bbox": None,
                    "confidence": None,
                    "status": "failed",
                    "error": "OCR backend unavailable",
                    "source_type": "ocr",
                    "blocks": [],
                }],
                "failed_pages": [1],
            },
            "error_code": "OCR_PROCESSING_ERROR",
            "message": "OCR backend unavailable",
        },
    )
    failed = document_index.reprocess_document(record["file_id"])

    assert failed["ok"] is True
    assert failed["data"]["status"] == "partial_success"
    assert failed["data"]["failed_pages"] == [1]
    assert failed["data"]["page_results"][0]["refresh_status"] == "failed"
    assert database.get_document_chunks(record["file_id"]) == old_chunks
    assert database.get_document_ocr_regions(record["file_id"]) == old_regions
    lifecycle = get_file_lifecycle(record["file_id"])["data"]
    assert lifecycle["status"] == "QUERYABLE"


async def test_image_async_ocr_failure_preserves_old_regions_and_retry_only_page_one(
    visual_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocr_service, "_create_engine", PrintedEngine)
    uploaded = save_uploaded_file(
        "异步视觉.png",
        _image_bytes("PNG"),
        declared_mime_type="image/png",
    )
    file_id = uploaded["data"]["file_id"]
    old_chunks = database.get_document_chunks(file_id)
    old_regions = database.get_document_ocr_regions(file_id)

    failed_page = {
        "page_no": 1,
        "text": "",
        "bbox": None,
        "confidence": None,
        "status": "failed",
        "error": "temporary OCR failure",
        "source_type": "ocr",
        "blocks": [],
    }
    monkeypatch.setattr(
        document_index.ocr_service,
        "ocr_image",
        lambda path, file_id=None: {
            "ok": False,
            "data": {
                "status": "failed",
                "pages": [failed_page],
                "regions": [],
                "failed_pages": [1],
            },
            "error_code": "OCR_PROCESSING_ERROR",
            "message": "temporary OCR failure",
        },
    )
    queued = enqueue_async_task(
        {
            "session_id": "v4-visual-async",
            "task_type": "ocr",
            "payload": {"file_id": file_id},
        }
    )
    task_id = queued["data"]["task"]["task_id"]
    failed = await run_async_task(task_id)

    assert failed["data"]["task"]["display_status"] == "failed"
    assert database.get_document_chunks(file_id) == old_chunks
    assert database.get_document_ocr_regions(file_id) == old_regions
    assert get_file_lifecycle(file_id)["data"]["status"] == "QUERYABLE"
    assert get_file_lifecycle(file_id)["data"]["error"]["failure_stage"] == "ocr"

    retried = retry_async_task(task_id)
    retry_payload = retried["data"]["task"]["checkpoint_data"]["async_task"]["payload"]
    assert retry_payload["pages"] == [1]
    monkeypatch.setattr(
        document_index.ocr_service,
        "ocr_image",
        lambda path, file_id=None: _ocr_success(_ocr_page(1, "重试恢复 OCR 2026")),
    )
    recovered = await run_async_task(task_id)

    assert recovered["data"]["task"]["display_status"] == "success"
    assert [chunk["chunk_text"] for chunk in database.get_document_chunks(file_id)] == [
        "重试恢复 OCR 2026"
    ]
    assert database.get_document_ocr_regions(file_id)[0]["text"] == "重试恢复 OCR 2026"
