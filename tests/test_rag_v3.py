"""V3.6 Hybrid Retrieval keyword, semantic, metadata and fallback tests."""

import hashlib
from pathlib import Path

import pytest

from backend import database
from backend.services import embedding_service
from backend.services.document_index import retrieve_document
from backend.tools import excel_utils


@pytest.fixture
def rag_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    pdf_id = _register_document(upload_dir, "资助办法.pdf", "pdf")
    word_id = _register_document(upload_dir, "申请说明.docx", "word")
    _replace_chunks(
        pdf_id,
        [
            (1, "奖学金申请条件包括成绩证明。"),
            (2, "家庭困难学生可获得助学资金支持。"),
        ],
    )
    _replace_chunks(
        word_id,
        [
            (1, "普通课程选修说明。"),
            (2, "奖学金申请条件需要导师签字。"),
        ],
    )
    return {"pdf": pdf_id, "word": word_id}


def _register_document(directory: Path, name: str, file_type: str) -> str:
    (directory / name).write_bytes(b"fixture")
    database.register_file(
        file_name=name,
        file_type=file_type,
        file_path=f"data/uploads/{name}",
        lifecycle_status="ready",
        parse_status="parsed",
        queryable=True,
        index_status="indexed",
    )
    return database.get_file_record(name)["file_id"]


def _replace_chunks(file_id: str, pages: list[tuple[int, str]]) -> None:
    chunks = []
    for index, (page, text) in enumerate(pages):
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunks.append(
            {
                "chunk_id": hashlib.sha256(
                    f"{file_id}:{index}:{text_hash}".encode("utf-8")
                ).hexdigest()[:32],
                "file_id": file_id,
                "page_no": page,
                "chunk_index": index,
                "chunk_text": text,
                "text_hash": text_hash,
                "metadata": {"source_type": "legacy-test", "page_no": page},
            }
        )
    database.replace_document_chunks(file_id, chunks)


def test_keyword_retrieval_keeps_fts5_and_evidence_compatible(rag_files: dict) -> None:
    keyword_rows = database.search_document_chunks(
        file_ids=[rag_files["pdf"]], query="奖学金申请条件", limit=5
    )

    result = retrieve_document(
        {"file_id": rag_files["pdf"]}, "奖学金申请条件", top_k=5
    )

    assert keyword_rows
    assert result["ok"] is True
    assert result["data"]["retrieval_mode"] == "hybrid"
    evidence = result["data"]["evidence"][0]
    assert evidence["file_id"] == rag_files["pdf"]
    assert "奖学金申请条件" in evidence["text_excerpt"]
    assert "block_id" not in evidence  # Legacy chunks retain their prior Evidence shape.


def test_semantic_query_uses_embedding_when_fts_has_no_phrase(
    rag_files: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SemanticProvider:
        def embed_query(self, text: str) -> list[float]:
            return [1.0, 0.0]

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [
                [1.0, 0.0] if "助学资金" in text else [0.0, 1.0]
                for text in texts
            ]

    monkeypatch.setattr(
        embedding_service,
        "get_default_embedding_provider",
        lambda: SemanticProvider(),
    )
    assert database.search_document_chunks(
        file_ids=[rag_files["pdf"]], query="经济援助", limit=5
    ) == []

    result = retrieve_document(
        {"file_id": rag_files["pdf"]}, "经济援助", top_k=1
    )

    assert result["ok"] is True
    assert result["data"]["retrieval_mode"] == "hybrid"
    assert result["data"]["fallback_used"] is False
    assert "助学资金" in result["data"]["evidence"][0]["text_excerpt"]


def test_metadata_filter_combines_file_type_and_page(rag_files: dict) -> None:
    result = retrieve_document(
        {"file_type": "word", "page": 2},
        "奖学金申请条件",
        top_k=5,
    )

    assert result["ok"] is True
    evidence = result["data"]["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["file_id"] == rag_files["word"]
    assert evidence[0]["page_no"] == 2


def test_vector_failure_falls_back_to_original_fts5(
    rag_files: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailedProvider:
        def embed_query(self, text: str) -> list[float]:
            raise RuntimeError("simulated embedding failure")

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("simulated embedding failure")

    monkeypatch.setattr(
        embedding_service,
        "get_default_embedding_provider",
        lambda: FailedProvider(),
    )

    result = retrieve_document(
        {"file_id": rag_files["pdf"]}, "奖学金申请条件", top_k=5
    )

    assert result["ok"] is True
    assert result["data"]["retrieval_mode"] == "keyword_fallback"
    assert result["data"]["fallback_used"] is True
    assert result["warnings"] == ["VECTOR_RETRIEVAL_FAILED"]
    assert "奖学金申请条件" in result["data"]["evidence"][0]["text_excerpt"]

