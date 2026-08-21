"""V3.6 keyword/vector retrieval, fusion, rerank and FTS fallback."""

import math
import re
from typing import Any

from backend import database
from backend.services import embedding_service
from backend.services.embedding_service import EmbeddingProvider


RRF_K = 60
VECTOR_MIN_SCORE = 0.12


def hybrid_retrieve(
    *,
    file_ids: list[str],
    query: str,
    limit: int,
    page_no: int | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> dict[str, Any]:
    """Retrieve, fuse and rerank while falling back to the original FTS5 path."""
    candidate_limit = max(20, min(100, limit * 4))
    keyword_rows = database.search_document_chunks_filtered(
        file_ids=file_ids,
        query=query,
        limit=candidate_limit,
        page_no=page_no,
    )
    try:
        provider = embedding_provider or embedding_service.get_default_embedding_provider()
        vector_rows = _vector_retrieve(
            file_ids=file_ids,
            query=query,
            limit=candidate_limit,
            page_no=page_no,
            provider=provider,
        )
        rows = _rerank(_fuse(keyword_rows, vector_rows), query)[:limit]
        return {
            "rows": rows,
            "retrieval_mode": "hybrid",
            "fallback_used": False,
            "warning": None,
        }
    except Exception:
        rows = _keyword_fallback(keyword_rows, limit)
        return {
            "rows": rows,
            "retrieval_mode": "keyword_fallback",
            "fallback_used": True,
            "warning": "VECTOR_RETRIEVAL_FAILED",
        }


def _vector_retrieve(
    *,
    file_ids: list[str],
    query: str,
    limit: int,
    page_no: int | None,
    provider: EmbeddingProvider,
) -> list[dict[str, Any]]:
    chunks = [
        chunk
        for file_id in file_ids
        for chunk in database.get_document_chunks(file_id)
        if page_no is None or chunk.get("page_no") == page_no
    ]
    if not chunks:
        return []
    query_vector = provider.embed_query(query)
    document_vectors = provider.embed_documents(
        [str(chunk.get("chunk_text") or "") for chunk in chunks]
    )
    if len(document_vectors) != len(chunks):
        raise ValueError("embedding 返回数量与文档数量不一致")
    ranked = []
    for chunk, vector in zip(chunks, document_vectors):
        score = _cosine(query_vector, vector)
        if score < VECTOR_MIN_SCORE:
            continue
        item = dict(chunk)
        item["vector_score"] = score
        ranked.append(item)
    ranked.sort(
        key=lambda item: (
            -float(item["vector_score"]),
            str(item["file_id"]),
            int(item["chunk_index"]),
        )
    )
    return ranked[:limit]


def _fuse(
    keyword_rows: list[dict[str, Any]],
    vector_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}
    for source, rows in (("keyword", keyword_rows), ("vector", vector_rows)):
        for position, row in enumerate(rows, start=1):
            chunk_id = row["chunk_id"]
            item = fused.setdefault(
                chunk_id,
                {**row, "fusion_score": 0.0, "retrieval_sources": []},
            )
            item["fusion_score"] += 1.0 / (RRF_K + position)
            item["retrieval_sources"].append(source)
            if source == "vector":
                item["vector_score"] = float(row["vector_score"])
            else:
                item["keyword_position"] = position
                item["rank"] = float(row.get("rank", 0.0))
    return list(fused.values())


def _rerank(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    max_fusion = max(float(row["fusion_score"]) for row in rows) or 1.0
    query_terms = _terms(query)
    for row in rows:
        text_terms = _terms(str(row.get("chunk_text") or ""))
        overlap = (
            len(query_terms & text_terms) / len(query_terms)
            if query_terms
            else 0.0
        )
        keyword_score = 1.0 / float(row.get("keyword_position", 10_000))
        score = (
            0.5 * float(row["fusion_score"]) / max_fusion
            + 0.3 * max(0.0, float(row.get("vector_score", 0.0)))
            + 0.1 * overlap
            + 0.1 * keyword_score
        )
        row["retrieval_score"] = round(min(1.0, score), 8)
    rows.sort(
        key=lambda item: (
            -float(item["retrieval_score"]),
            str(item["file_id"]),
            int(item["chunk_index"]),
        )
    )
    return rows


def _keyword_fallback(
    rows: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    fallback = []
    for position, row in enumerate(rows[:limit], start=1):
        item = dict(row)
        item["retrieval_sources"] = ["keyword"]
        item["retrieval_score"] = round(1.0 / position, 8)
        fallback.append(item)
    return fallback


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("embedding 向量维度不一致")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _terms(text: str) -> set[str]:
    normalized = str(text).casefold()
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", normalized))
