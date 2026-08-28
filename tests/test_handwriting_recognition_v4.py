"""V4.3 fixed-sample pipeline tests; these are not OCR accuracy metrics."""

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from backend import database
from backend.runtime.async_task_runtime import enqueue_async_task, run_async_task
from backend.services import document_index, ocr_service
from backend.services.file_upload import save_uploaded_file
from backend.tools import excel_utils
from backend.tools import hybrid_tools


FIXTURES = Path(__file__).parent / "fixtures" / "v4_handwriting_cases.json"
pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _samples() -> dict[str, dict[str, Any]]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def _raw(sample: dict[str, Any], *, x: int = 2) -> dict[str, Any]:
    return {
        "bbox": [[x, 2], [x + 80, 2], [x + 80, 22], [x, 22]],
        "text": sample["text"],
        "confidence": sample["confidence"],
        "recognition_type": sample["recognition_type"],
        "source_model": "fixed-handwriting-adapter-v1",
        "source_parser": "handwriting-adapter",
    }


def _image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (160, 80), "white").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture()
def handwriting_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    ("case_id", "review_required"),
    [
        ("clear_handwriting", False),
        ("medium_handwriting", True),
        ("extremely_unclear_handwriting", True),
    ],
)
def test_fixed_clear_medium_and_extremely_unclear_handwriting(
    case_id: str, review_required: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = _samples()[case_id]
    monkeypatch.setattr(ocr_service, "_render_image", lambda path: object())

    class Engine:
        def __call__(self, image: object):
            return ([_raw(sample)], 0.01)

    monkeypatch.setattr(ocr_service, "_create_engine", Engine)
    result = ocr_service.ocr_visual(Path("fixed.png"), file_id="h" * 32)
    region = result["data"]["regions"][0]

    assert region["text"] == sample["text"]
    assert region["recognition_type"] == "handwritten"
    assert region["source_model"] == "fixed-handwriting-adapter-v1"
    assert region["status"] == sample["expected_status"]
    assert region["review_required"] is review_required
    assert region["safe_for_high_impact"] is (not review_required)
    if case_id == "extremely_unclear_handwriting":
        assert result["ok"] is False
        assert result["data"]["blocks"] == []


def test_mixed_printed_and_handwriting_regions_share_one_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handwritten = _samples()["clear_handwriting"]
    monkeypatch.setattr(ocr_service, "_render_image", lambda path: object())

    class MixedEngine:
        def __call__(self, image: object):
            return ([
                {
                    "bbox": [1, 1, 90, 18], "text": "打印标题", "confidence": 0.98,
                    "recognition_type": "printed", "source_model": "printed-v1",
                    "source_parser": "printed-adapter",
                },
                _raw(handwritten, x=3),
            ], 0.01)

    monkeypatch.setattr(ocr_service, "_create_engine", MixedEngine)
    result = ocr_service.ocr_visual(Path("mixed.jpg"), file_id="m" * 32)

    assert result["ok"] is True
    assert result["data"]["recognition_type"] == "mixed"
    assert [item["recognition_type"] for item in result["data"]["regions"]] == [
        "printed", "handwritten"
    ]


@pytest.mark.parametrize("case_id", ["low_confidence_name", "low_confidence_phone"])
def test_low_confidence_identity_fields_require_review_and_are_not_safe_matches(
    case_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = _samples()[case_id]
    monkeypatch.setattr(ocr_service, "_render_image", lambda path: object())

    class KeyFieldEngine:
        def __call__(self, image: object):
            return ([_raw(sample)], 0.01)

    monkeypatch.setattr(ocr_service, "_create_engine", KeyFieldEngine)
    region = ocr_service.ocr_visual(Path("key.png"))["data"]["regions"][0]

    assert region["key_field_type"] == sample["expected_key_field_type"]
    assert region["review_required"] is True
    assert region["safe_for_high_impact"] is False
    assert region["safe_for_identity_match"] is False
    assert "LOW_CONFIDENCE_KEY_FIELD" in region["review_reason"]


def test_partial_region_failure_is_reported_without_losing_successful_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = _samples()["clear_handwriting"]
    monkeypatch.setattr(ocr_service, "_render_image", lambda path: object())

    class PartialEngine:
        def __call__(self, image: object):
            failed = _raw(_samples()["extremely_unclear_handwriting"], x=90)
            failed.update({"text": "", "status": "failed", "error": "unreadable region"})
            return ([_raw(sample), failed], 0.01)

    monkeypatch.setattr(ocr_service, "_create_engine", PartialEngine)
    result = ocr_service.ocr_visual(Path("partial.png"), file_id="p" * 32)

    assert result["ok"] is True
    assert result["data"]["status"] == "partial_success"
    assert len(result["data"]["failed_region_ids"]) == 1
    assert result["data"]["blocks"][0]["text"] == sample["text"]


def test_model_conflict_preserves_both_sources_and_never_silently_selects(
    handwriting_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocr_service, "_render_image", lambda path: object())

    class ConflictEngine:
        def __call__(self, image: object):
            return ([{
                "bbox": [2, 2, 120, 24], "text": "姓名：王伟", "confidence": 0.91,
                "recognition_type": "mixed", "source_model": "coordinator",
                "source_parser": "visual-coordinator",
                "candidates": [
                    {"text": "姓名：王伟", "confidence": 0.91,
                     "recognition_type": "printed", "source_model": "printed-v1",
                     "source_parser": "printed-adapter"},
                    {"text": "姓名：王薇", "confidence": 0.87,
                     "recognition_type": "handwritten", "source_model": "hand-v1",
                     "source_parser": "handwriting-adapter"}
                ]
            }], 0.01)

    monkeypatch.setattr(ocr_service, "_create_engine", ConflictEngine)
    uploaded = save_uploaded_file("冲突.png", _image_bytes(), declared_mime_type="image/png")
    regions = database.get_document_ocr_regions(uploaded["data"]["file_id"])

    assert len(regions) == 1
    assert regions[0]["text"] == ""
    assert regions[0]["status"] == "low_confidence"
    assert regions[0]["review_required"] is True
    assert regions[0]["safe_for_identity_match"] is False
    assert {item["source_model"] for item in regions[0]["conflict_sources"]} == {
        "printed-v1", "hand-v1"
    }


async def test_failed_region_retry_crops_only_that_region_and_keeps_region_id(
    handwriting_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unclear = _samples()["extremely_unclear_handwriting"]

    class UnclearEngine:
        def __call__(self, image: object):
            return ([_raw(unclear)], 0.01)

    monkeypatch.setattr(ocr_service, "_create_engine", UnclearEngine)
    uploaded = save_uploaded_file("局部重试.png", _image_bytes(), declared_mime_type="image/png")
    file_id = uploaded["data"]["file_id"]
    old_region = database.get_document_ocr_regions(file_id)[0]
    calls: list[tuple[int, ...]] = []

    class RecoveredEngine:
        def __call__(self, image: Any):
            calls.append(tuple(image.shape))
            recovered = dict(_raw(_samples()["clear_handwriting"]))
            recovered["text"] = "局部重试已恢复"
            return ([recovered], 0.01)

    monkeypatch.setattr(ocr_service, "_create_engine", RecoveredEngine)
    queued = enqueue_async_task({
        "session_id": "v4.3-region-retry",
        "task_type": "ocr",
        "payload": {"file_id": file_id, "region_ids": [old_region["region_id"]]},
    })
    result = await run_async_task(queued["data"]["task"]["task_id"])

    assert result["ok"] is True
    assert len(calls) == 1
    recovered = database.get_document_ocr_regions(file_id)[0]
    assert recovered["region_id"] == old_region["region_id"]
    assert recovered["text"] == "局部重试已恢复"
    assert recovered["status"] == "success"
    assert [chunk["chunk_text"] for chunk in database.get_document_chunks(file_id)] == [
        "局部重试已恢复"
    ]


def test_handwriting_thresholds_are_configurable_not_model_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = _samples()["medium_handwriting"]
    monkeypatch.setenv("HANDWRITING_USABLE_CONFIDENCE", "0.75")
    monkeypatch.setenv("HANDWRITING_REVIEW_CONFIDENCE", "0.80")
    monkeypatch.setenv("HANDWRITING_KEY_FIELD_CONFIDENCE", "0.90")
    monkeypatch.setattr(ocr_service, "_render_image", lambda path: object())

    class Engine:
        def __call__(self, image: object):
            return ([_raw(sample)], 0.01)

    monkeypatch.setattr(ocr_service, "_create_engine", Engine)
    region = ocr_service.ocr_visual(Path("configured.png"))["data"]["regions"][0]

    assert region["status"] == "low_confidence"
    assert region["source_model"] == "fixed-handwriting-adapter-v1"


def test_unknown_recognition_type_is_preserved_for_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ocr_service, "_render_image", lambda path: object())

    class UnknownEngine:
        def __call__(self, image: object):
            return ([{
                "bbox": [1, 1, 80, 20], "text": "类型无法确认", "confidence": 0.93,
                "recognition_type": "unclassified-backend-value",
                "source_model": "unknown-v1", "source_parser": "unknown-adapter",
            }], 0.01)

    monkeypatch.setattr(ocr_service, "_create_engine", UnknownEngine)
    region = ocr_service.ocr_visual(Path("unknown.png"))["data"]["regions"][0]

    assert region["recognition_type"] == "unknown"
    assert region["review_required"] is True
    assert region["safe_for_high_impact"] is False


def test_high_impact_rule_engine_rejects_unsafe_handwriting_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hybrid_tools,
        "_chunk_text",
        lambda evidence: "一等奖学金 平均成绩不低于90分",
    )
    extracted = hybrid_tools._extract_rules("一等奖学金", [{
        "result": {
            "ok": True,
            "evidence": [{
                "evidence_id": "unsafe-handwriting",
                "safe_for_high_impact": False,
                "review_required": True,
            }],
        }
    }])

    assert extracted == {"conditions": [], "ambiguous": True}
