"""V3.5 DocumentBlock title, paragraph, table and legacy compatibility tests."""

from pathlib import Path

import pytest
from docx import Document

from backend import database
from backend.document_blocks import DocumentBlock, blocks_from_chunks
from backend.services.document_index import index_document, retrieve_document
from backend.tools import excel_utils


@pytest.fixture
def indexed_word(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    path = upload_dir / "结构化材料.docx"
    document = Document()
    document.add_heading("材料提交说明", level=1)
    document.add_paragraph("正文规定材料提交截止日期为六月三十日。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "材料类型"
    table.cell(0, 1).text = "份数"
    table.cell(1, 0).text = "成绩单"
    table.cell(1, 1).text = "一份"
    document.save(path)
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    database.register_file(
        file_name=path.name,
        file_type="word",
        file_path=f"data/uploads/{path.name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
    )
    record = database.get_file_record(path.name)
    result = index_document(record["file_id"])
    assert result["ok"] is True
    return {"record": record, "result": result}


def test_title_is_persisted_as_document_block(indexed_word: dict) -> None:
    blocks = [DocumentBlock.model_validate(item) for item in indexed_word["result"]["data"]["blocks"]]
    title = next(block for block in blocks if block.block_type == "title")

    assert title.content == "材料提交说明"
    assert title.file_id == indexed_word["record"]["file_id"]
    assert title.page_no is None
    assert title.bbox is None
    assert title.confidence is None
    chunks = database.get_document_chunks(title.file_id)
    title_chunk = next(chunk for chunk in chunks if chunk["metadata"].get("block_id") == title.block_id)
    assert title_chunk["chunk_text"] == title.content
    assert title_chunk["metadata"]["block_type"] == "title"


def test_paragraph_chunk_produces_block_referenced_evidence(indexed_word: dict) -> None:
    file_id = indexed_word["record"]["file_id"]
    chunks = database.get_document_chunks(file_id)
    paragraph_chunk = next(
        chunk for chunk in chunks if chunk["metadata"].get("block_type") == "paragraph"
    )

    retrieved = retrieve_document({"file_id": file_id}, "材料提交截止日期")

    assert retrieved["ok"] is True
    evidence = retrieved["data"]["evidence"][0]
    assert evidence["chunk_id"] == paragraph_chunk["chunk_id"]
    assert evidence["block_id"] == paragraph_chunk["metadata"]["block_id"]
    assert paragraph_chunk["metadata"]["block"]["content"] == (
        "正文规定材料提交截止日期为六月三十日。"
    )


def test_table_and_cells_are_distinct_blocks_and_generate_chunks(
    indexed_word: dict,
) -> None:
    blocks = [DocumentBlock.model_validate(item) for item in indexed_word["result"]["data"]["blocks"]]
    table = next(block for block in blocks if block.block_type == "table")
    cells = [block for block in blocks if block.block_type == "cell"]

    assert table.content == "材料类型\t份数\n成绩单\t一份"
    assert [cell.content for cell in cells] == ["材料类型", "份数", "成绩单", "一份"]
    chunks = database.get_document_chunks(indexed_word["record"]["file_id"])
    assert {chunk["metadata"]["block_type"] for chunk in chunks} >= {
        "table",
        "cell",
    }
    assert all(
        any(chunk["metadata"].get("block_id") == block.block_id for chunk in chunks)
        for block in [table, *cells]
    )


def test_legacy_chunk_without_block_metadata_remains_readable() -> None:
    legacy = {
        "chunk_id": "legacy-chunk",
        "file_id": "legacy-file",
        "page_no": 1,
        "chunk_index": 0,
        "chunk_text": "旧数据",
        "text_hash": "legacy-hash",
        "metadata": {"source_type": "pdf", "page_no": 1},
    }

    assert blocks_from_chunks([legacy]) == []
    assert legacy["metadata"] == {"source_type": "pdf", "page_no": 1}

