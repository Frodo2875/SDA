"""V3.6 Hybrid Retrieval keyword, semantic, metadata and fallback tests."""

import hashlib
import time
from pathlib import Path

import pytest

from backend import database
from backend.services import embedding_service
from backend.services.hybrid_retrieval import hybrid_retrieve
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


def _replace_metadata_chunks(
    file_id: str, items: list[tuple[int, str, dict]]
) -> None:
    chunks = []
    for index, (page, text, metadata) in enumerate(items):
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunks.append({
            "chunk_id": hashlib.sha256(f"{file_id}:{index}:{text_hash}".encode()).hexdigest()[:32],
            "file_id": file_id, "page_no": page, "chunk_index": index,
            "chunk_text": text, "text_hash": text_hash,
            "metadata": {"source_type": "rag-v3.18", "page_no": page, **metadata},
        })
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


def test_r201_exact_identifier_and_scores_are_preserved(rag_files: dict) -> None:
    _replace_metadata_chunks(
        rag_files["pdf"],
        [(1, "规则精确标识 R201-ALPHA-2026", {"year": 2026}),
         (2, "其他规则 R201-BETA-2026", {"year": 2026})],
    )
    result = retrieve_document(
        {"file_id": rag_files["pdf"], "year": 2026}, "R201-ALPHA-2026", top_k=1
    )
    assert result["data"]["status"] == "found"
    assert "R201-ALPHA-2026" in result["evidence"][0]["text_excerpt"]
    scores = result["data"]["score_details"][0]
    assert set(scores) == {
        "chunk_id", "keyword_score", "vector_score", "combined_score",
        "rerank_score", "final_score",
    }
    assert scores["keyword_score"] > 0
    assert scores["combined_score"] > 0


def test_r203_year_student_and_document_metadata_are_hard_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    old_id = _register_document(upload_dir, "2025奖学金办法.pdf", "pdf")
    current_id = _register_document(upload_dir, "2026奖学金办法.pdf", "pdf")
    _replace_metadata_chunks(old_id, [(1, "2025年办法：平均分八十分。", {
        "year": 2025, "student_id": "S001", "document_metadata": {"category": "scholarship"}
    })])
    _replace_metadata_chunks(current_id, [(1, "2026年办法：平均分九十分。", {
        "year": 2026, "student_id": "S001", "document_metadata": {"category": "scholarship"}
    })])

    class AllSemanticProvider:
        def embed_query(self, text):
            return [1.0]

        def embed_documents(self, texts):
            return [[1.0] for _ in texts]

    monkeypatch.setattr(
        embedding_service, "get_default_embedding_provider", lambda: AllSemanticProvider()
    )
    result = retrieve_document(
        {
            "file_ids": [old_id, current_id],
            "student_id": "S001",
            "document_metadata": {"category": "scholarship"},
        },
        "只根据2026年办法",
        top_k=5,
    )
    assert result["data"]["metadata_filters"] == {
        "category": "scholarship", "year": 2026, "student_id": "S001"
    }
    assert {evidence["file_id"] for evidence in result["evidence"]} == {current_id}
    assert all("2025" not in evidence["text_excerpt"] for evidence in result["evidence"])


def test_r204_retrieves_top_n_then_reranks_to_top_k(rag_files: dict) -> None:
    _replace_chunks(
        rag_files["pdf"],
        [(index, f"奖学金规则候选 {index}") for index in range(1, 7)],
    )

    class AllProvider:
        def embed_query(self, text):
            return [1.0]

        def embed_documents(self, texts):
            return [[1.0] for _ in texts]

    observed = []

    class ReverseReranker:
        def rerank(self, query, candidates):
            observed.append(len(candidates))
            return [
                {"chunk_id": row["chunk_id"], "score": float(index)}
                for index, row in enumerate(reversed(candidates), start=1)
            ]

    result = hybrid_retrieve(
        file_ids=[rag_files["pdf"]], query="奖学金规则候选", limit=2,
        embedding_provider=AllProvider(), reranker=ReverseReranker(),
    )
    assert observed == [6]
    assert result["top_n_count"] == 6
    assert len(result["rows"]) == 2
    assert all("rerank_score" in row for row in result["rows"])


def test_r205_multiple_evidence_contains_only_final_candidates(rag_files: dict) -> None:
    result = retrieve_document({}, "奖学金申请条件", top_k=2)
    assert result["data"]["status"] == "found"
    assert len(result["evidence"]) == 2
    final_ids = {item["chunk_id"] for item in result["data"]["score_details"]}
    assert {item["chunk_id"] for item in result["evidence"]} == final_ids


@pytest.mark.parametrize("failure_kind", ["exception", "invalid", "timeout"])
def test_rerank_failure_falls_back_to_hybrid_order(
    rag_files: dict, failure_kind: str
) -> None:
    class AllProvider:
        def embed_query(self, text):
            return [1.0]

        def embed_documents(self, texts):
            return [[1.0] for _ in texts]

    class FailedReranker:
        def rerank(self, query, candidates):
            if failure_kind == "exception":
                raise RuntimeError("reranker unavailable")
            if failure_kind == "timeout":
                time.sleep(0.05)
                return []
            return [{"chunk_id": "fabricated", "score": 1.0}]

    result = hybrid_retrieve(
        file_ids=[rag_files["pdf"]], query="奖学金申请条件", limit=2,
        embedding_provider=AllProvider(), reranker=FailedReranker(),
        rerank_timeout_seconds=0.01,
    )
    assert result["retrieval_mode"] == "hybrid"
    assert result["rerank_fallback"] is True
    assert result["fallback_used"] is True
    assert result["warning"] == "RERANK_FALLBACK"
    assert result["fallback_reason"]
    assert result["rows"]
    authoritative_ids = {
        chunk["chunk_id"] for chunk in database.get_document_chunks(rag_files["pdf"])
    }
    assert {row["chunk_id"] for row in result["rows"]} <= authoritative_ids


def test_r210_no_result_returns_no_evidence(rag_files: dict) -> None:
    result = retrieve_document(
        {"file_id": rag_files["pdf"], "year": 2099}, "不存在的规则", top_k=5
    )
    assert result["data"]["status"] == "not_found"
    assert result["evidence"] == []
    assert result["data"]["score_details"] == []
