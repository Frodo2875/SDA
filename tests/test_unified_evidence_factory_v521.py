"""V5.2.1 canonical Evidence Factory integration tests."""

from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from backend import database
from backend.agent import _collect_unified_evidence
from backend.document_blocks import block_to_chunks, make_document_block
from backend.evidence import UnifiedEvidence
from backend.services.document_index import retrieve_document
from backend.services.visual_understanding import extract_key_fields
from backend.services.web_retrieval import WebRetrievalService
from backend.tools import excel_utils
from backend.tools.schema_tools import inspect_excel
from backend.tools.table_tools import query_table


def _register_local(tmp_path: Path, name: str, file_type: str) -> dict[str, Any]:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(exist_ok=True)
    path = upload_dir / name
    if not path.exists():
        path.write_bytes(b"fixture")
    database.register_file(
        file_name=name,
        file_type=file_type,
        file_path=f"data/uploads/{name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
        index_status="indexed" if file_type in {"pdf", "image"} else "not_required",
    )
    return database.get_file_record(name)


def test_local_pdf_retrieval_generates_canonical_evidence_before_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    record = _register_local(tmp_path, "factory.pdf", "pdf")
    block = make_document_block(
        file_id=record["file_id"],
        sequence=0,
        block_type="paragraph",
        content="Factory PDF canonical evidence.",
        page_no=2,
        source_parser="fixture",
    )
    database.replace_document_chunks(
        record["file_id"], block_to_chunks(block, source_type="pdf")
    )

    result = retrieve_document(
        {"file_id": record["file_id"]}, "canonical evidence", top_k=1
    )
    canonical = result["data"]["unified_evidence"][0]
    compatibility = result["data"]["evidence"][0]

    assert UnifiedEvidence.model_validate(canonical).source_type == "LOCAL"
    assert canonical["evidence_version"] == "4.0"
    assert canonical["block_id"] == block.block_id
    assert compatibility["source_type"] == "unstructured"
    assert compatibility["evidence_id"] == canonical["evidence_id"]


@pytest.mark.parametrize("file_type", ["excel", "csv"])
def test_excel_and_csv_queries_generate_canonical_evidence_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_type: str
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(exist_ok=True)
    suffix = "xlsx" if file_type == "excel" else "csv"
    name = f"factory-table.{suffix}"
    path = upload_dir / name
    if file_type == "excel":
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Sheet1"
        sheet.append(["项目", "金额"])
        sheet.append(["A", 120])
        workbook.save(path)
        workbook.close()
    else:
        path.write_text("项目,金额\nA,120\n", encoding="utf-8")
    database.register_file(
        file_name=name,
        file_type=file_type,
        file_path=f"data/uploads/{name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
        index_status="not_required",
    )
    record = database.get_file_record(name)
    assert inspect_excel(record["file_id"])["ok"] is True

    sheet_name = "Sheet1" if file_type == "excel" else "CSV"
    result = query_table(
        record["file_id"], sheet_name,
        filters=[{"field": "项目", "op": "=", "value": "A"}],
        select=["金额"],
    )
    canonical = result["unified_evidence"][0]
    compatibility = result["evidence_chain"][0]

    assert canonical["source_type"] == "LOCAL"
    assert canonical["legacy_source_type"] == "structured"
    assert canonical["cell"] == "B2"
    assert compatibility["source_type"] == "structured"
    assert compatibility["evidence_id"] == canonical["evidence_id"]


def test_image_ocr_kie_generates_canonical_evidence_before_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    record = _register_local(tmp_path, "factory.png", "image")
    block = {
        "block_id": "factory-image-block",
        "file_id": record["file_id"],
        "page_no": 1,
        "image_no": 1,
        "block_type": "paragraph",
        "text": "姓名：张三",
        "bbox": [10.0, 20.0, 180.0, 50.0],
        "confidence": 0.96,
        "parent_id": None,
        "source_parser": "fixture-ocr",
        "source_model": "fixture-model",
        "recognition_type": "printed",
        "source_region_ids": ["factory-region"],
        "review_required": False,
        "safe_for_high_impact": True,
        "safe_for_identity_match": True,
        "status": "normal",
        "warnings": [],
    }

    result = extract_key_fields(
        record["file_id"], domain="student", blocks=[block]
    )
    canonical = result["data"]["unified_evidence"][0]
    compatibility = result["data"]["fields"][0]["evidence"]

    assert canonical["source_type"] == "LOCAL"
    assert canonical["locator_type"] == "image"
    assert canonical["region_id"] == "factory-region"
    assert compatibility["source_type"] == "unstructured"
    assert compatibility["evidence_id"] == canonical["evidence_id"]


def test_web_and_mixed_chains_use_factory_canonical_evidence() -> None:
    class Provider:
        name = "factory-provider"

        def search(self, query, scope):
            return [{
                "title": "Factory Web",
                "url": "https://example.com/factory",
                "snippet": "Factory Web evidence.",
                "source": self.name,
                "metadata": {},
            }]

    web_result = WebRetrievalService(search_provider=Provider()).retrieve(
        query="factory"
    )
    web = web_result["unified_evidence"][0]
    local = UnifiedEvidence(
        evidence_id="factory-local",
        source_type="LOCAL",
        content="Local evidence.",
        authority="unknown",
        freshness="not_applicable",
        metadata={"legacy_evidence_version": "3.0"},
        legacy_source_type="unstructured",
        file_id="local-file",
        file_name="local.pdf",
        chunk_id="local-chunk",
        value_summary="Local evidence.",
        trust_level="untrusted_document_data",
        instruction_authority="none",
        approval_authority="none",
        can_trigger_tool=False,
        can_change_tool_risk=False,
        can_approve=False,
    ).model_dump(mode="json", exclude_none=True)
    mixed = _collect_unified_evidence([
        {"name": "retrieve_document", "result": {"unified_evidence": [local]}},
        {"name": "retrieve_web", "result": web_result},
    ])

    assert web["source_type"] == "WEB"
    assert {item["source_type"] for item in mixed} == {"LOCAL", "WEB"}
    assert all(item["evidence_version"] == "4.0" for item in mixed)
    assert all(UnifiedEvidence.model_validate(item) for item in mixed)
