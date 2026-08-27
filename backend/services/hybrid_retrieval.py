"""Reliable metadata-filtered keyword/vector fusion and bounded reranking."""

import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Protocol

from backend import database
from backend.services import embedding_service
from backend.services.embedding_service import EmbeddingProvider


RRF_K = 60
VECTOR_MIN_SCORE = 0.12
RERANK_TIMEOUT_SECONDS = 2.0


class RerankProvider(Protocol):
    def rerank(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...


class LocalReranker:
    """Deterministic bounded reranker over authoritative hybrid candidates."""

    def rerank(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        max_combined = max(float(row["combined_score"]) for row in candidates) or 1.0
        query_terms = _terms(query)
        ranked = []
        for row in candidates:
            text_terms = _terms(str(row.get("chunk_text") or ""))
            overlap = len(query_terms & text_terms) / len(query_terms) if query_terms else 0.0
            score = (
                0.6 * float(row["combined_score"]) / max_combined
                + 0.25 * max(0.0, float(row.get("vector_score", 0.0)))
                + 0.15 * overlap
            )
            ranked.append({"chunk_id": row["chunk_id"], "score": round(min(1.0, score), 8)})
        ranked.sort(key=lambda item: (-float(item["score"]), str(item["chunk_id"])))
        return ranked


def hybrid_retrieve(
    *,
    file_ids: list[str],
    query: str,
    limit: int,
    page_no: int | None = None,
    metadata_filters: dict[str, Any] | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    reranker: RerankProvider | None = None,
    rerank_timeout_seconds: float = RERANK_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Hard-filter, recall Top-N, rerank Top-N, and return only authoritative Top-K."""
    retrieval_started = time.perf_counter()
    candidate_limit = max(20, min(100, limit * 4))
    filters = dict(metadata_filters or {})
    keyword_rows = database.search_document_chunks_filtered(
        file_ids=file_ids,
        query=query,
        limit=candidate_limit,
        page_no=page_no,
        metadata_filters=filters,
    )
    try:
        provider = embedding_provider or embedding_service.get_default_embedding_provider()
        vector_rows = _vector_retrieve(
            file_ids=file_ids,
            query=query,
            limit=candidate_limit,
            page_no=page_no,
            metadata_filters=filters,
            provider=provider,
        )
    except Exception as exc:
        rows = _keyword_fallback(keyword_rows, limit)
        retrieval_duration_ms = _elapsed_ms(retrieval_started)
        return {
            "rows": rows,
            "retrieval_mode": "keyword_fallback",
            "fallback_used": True,
            "warning": "VECTOR_RETRIEVAL_FAILED",
            "fallback_reason": _reason(exc, "VECTOR_RETRIEVAL_FAILED"),
            "rerank_fallback": False,
            "rerank_fallback_reason": None,
            "keyword_candidate_count": len(keyword_rows),
            "vector_candidate_count": 0,
            "top_n_count": len(keyword_rows),
            "top_k_count": len(rows),
            "hybrid_retrieval_duration_ms": retrieval_duration_ms,
            "rerank_duration_ms": None,
        }

    hybrid_candidates = _hybrid_order(_fuse(keyword_rows, vector_rows))[:candidate_limit]
    rerank_fallback = False
    rerank_reason = None
    rerank_started = time.perf_counter()
    try:
        rows = _run_reranker(
            reranker or LocalReranker(), query, hybrid_candidates,
            timeout_seconds=rerank_timeout_seconds,
        )[:limit]
    except Exception as exc:
        rerank_fallback = True
        rerank_reason = _reason(exc, "RERANK_FAILED")
        rows = [dict(row) for row in hybrid_candidates[:limit]]
        for row in rows:
            row["retrieval_score"] = row["combined_score"]
    rerank_duration_ms = _elapsed_ms(rerank_started)
    return {
        "rows": rows,
        "retrieval_mode": "hybrid",
        "fallback_used": rerank_fallback,
        "warning": "RERANK_FALLBACK" if rerank_fallback else None,
        "fallback_reason": rerank_reason,
        "rerank_fallback": rerank_fallback,
        "rerank_fallback_reason": rerank_reason,
        "keyword_candidate_count": len(keyword_rows),
        "vector_candidate_count": len(vector_rows),
        "top_n_count": len(hybrid_candidates),
        "top_k_count": len(rows),
        "hybrid_retrieval_duration_ms": _elapsed_ms(retrieval_started),
        "rerank_duration_ms": rerank_duration_ms,
    }


def _vector_retrieve(
    *,
    file_ids: list[str],
    query: str,
    limit: int,
    page_no: int | None,
    metadata_filters: dict[str, Any],
    provider: EmbeddingProvider,
) -> list[dict[str, Any]]:
    chunks = [
        chunk
        for file_id in file_ids
        for chunk in database.get_document_chunks(file_id)
        if (page_no is None or chunk.get("page_no") == page_no)
        and _matches_metadata(chunk.get("metadata") or {}, metadata_filters)
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
        item["vector_score"] = round(score, 8)
        ranked.append(item)
    ranked.sort(
        key=lambda item: (
            -float(item["vector_score"]), str(item["file_id"]), int(item["chunk_index"])
        )
    )
    return ranked[:limit]


def _fuse(
    keyword_rows: list[dict[str, Any]], vector_rows: list[dict[str, Any]]
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
                item["keyword_score"] = round(1.0 / position, 8)
                item["rank"] = float(row.get("rank", 0.0))
    for item in fused.values():
        item.setdefault("keyword_score", 0.0)
        item.setdefault("vector_score", 0.0)
        item["combined_score"] = round(float(item["fusion_score"]), 8)
    return list(fused.values())


def _hybrid_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda item: (
            -float(item["combined_score"]),
            -float(item.get("vector_score", 0.0)),
            str(item["file_id"]),
            int(item["chunk_index"]),
        ),
    )


def _run_reranker(
    reranker: RerankProvider,
    query: str,
    candidates: list[dict[str, Any]],
    *,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(reranker.rerank, query, [dict(row) for row in candidates])
    try:
        reranked = future.result(timeout=max(0.01, float(timeout_seconds)))
    except FutureTimeoutError as exc:
        future.cancel()
        raise TimeoutError("RERANK_TIMEOUT") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    if not isinstance(reranked, list):
        raise ValueError("RERANK_INVALID_RESULT: result must be a list")
    authoritative = {row["chunk_id"]: row for row in candidates}
    if len(reranked) != len(candidates):
        raise ValueError("RERANK_INVALID_RESULT: candidate count changed")
    seen = set()
    output = []
    for item in reranked:
        if not isinstance(item, dict):
            raise ValueError("RERANK_INVALID_RESULT: item must be an object")
        chunk_id = item.get("chunk_id")
        score = item.get("score")
        if chunk_id not in authoritative or chunk_id in seen:
            raise ValueError("RERANK_INVALID_RESULT: unknown or duplicate candidate")
        if not isinstance(score, (int, float)) or not math.isfinite(float(score)):
            raise ValueError("RERANK_INVALID_RESULT: score is invalid")
        seen.add(chunk_id)
        row = dict(authoritative[chunk_id])
        row["rerank_score"] = round(float(score), 8)
        row["retrieval_score"] = row["rerank_score"]
        output.append(row)
    return output


def _keyword_fallback(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    fallback = []
    for position, row in enumerate(rows[:limit], start=1):
        item = dict(row)
        item["retrieval_sources"] = ["keyword"]
        item["keyword_score"] = round(1.0 / position, 8)
        item["vector_score"] = 0.0
        item["combined_score"] = item["keyword_score"]
        item["retrieval_score"] = item["keyword_score"]
        fallback.append(item)
    return fallback


def _matches_metadata(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    nested = metadata.get("document_metadata")
    nested = nested if isinstance(nested, dict) else {}
    return all(
        (metadata.get(key) if key in metadata else nested.get(key)) == value
        for key, value in filters.items()
    )


def _reason(exc: Exception, default: str) -> str:
    message = str(exc).strip()
    return message[:300] if message else default


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


def _elapsed_ms(started: float) -> int:
    """Return an observed non-negative duration without inventing precision."""
    return max(0, int((time.perf_counter() - started) * 1000))
