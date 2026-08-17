"""SQLite repository for local document chunks and FTS5 retrieval."""

import json
import sqlite3
from collections.abc import Callable
from typing import Any


ConnectionFactory = Callable[[], sqlite3.Connection]


class DocumentRepository:
    """Atomically keep chunk rows and the derived FTS index synchronized."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def replace_for_file(self, file_id: str, chunks: list[dict[str, Any]]) -> None:
        with self._connection_factory() as connection:
            self._delete(connection, file_id)
            for chunk in chunks:
                metadata_json = json.dumps(
                    chunk.get("metadata") or {}, ensure_ascii=False, sort_keys=True
                )
                connection.execute(
                    """
                    INSERT INTO document_chunks (
                        chunk_id, file_id, page_no, chunk_index, chunk_text,
                        text_hash, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk["chunk_id"],
                        file_id,
                        chunk.get("page_no"),
                        chunk["chunk_index"],
                        chunk["chunk_text"],
                        chunk["text_hash"],
                        metadata_json,
                    ),
                )
                connection.execute(
                    "INSERT INTO document_chunks_fts (chunk_id, chunk_text) VALUES (?, ?)",
                    (chunk["chunk_id"], chunk["chunk_text"]),
                )

    def get_for_file(self, file_id: str) -> list[dict[str, Any]]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM document_chunks WHERE file_id = ? ORDER BY chunk_index",
                (file_id,),
            ).fetchall()
        return [self._deserialize(row) for row in rows]

    def delete_for_file(self, file_id: str) -> None:
        with self._connection_factory() as connection:
            self._delete(connection, file_id)

    def search(
        self,
        *,
        file_ids: list[str],
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not file_ids:
            return []
        placeholders = ",".join("?" for _ in file_ids)
        quoted_query = f'"{query.replace(chr(34), chr(34) * 2)}"'
        sql = f"""
            SELECT c.*, bm25(document_chunks_fts) AS rank
            FROM document_chunks_fts
            JOIN document_chunks AS c
              ON c.chunk_id = document_chunks_fts.chunk_id
            WHERE document_chunks_fts MATCH ?
              AND c.file_id IN ({placeholders})
            ORDER BY rank, c.chunk_index
            LIMIT ?
        """
        try:
            with self._connection_factory() as connection:
                rows = connection.execute(
                    sql, (quoted_query, *file_ids, limit)
                ).fetchall()
        except sqlite3.OperationalError:
            return self._search_like(file_ids=file_ids, query=query, limit=limit)
        results = []
        for row in rows:
            item = self._deserialize(row)
            item["rank"] = float(row["rank"])
            results.append(item)
        return results

    def _search_like(
        self, *, file_ids: list[str], query: str, limit: int
    ) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in file_ids)
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql = f"""
            SELECT *, 0.0 AS rank FROM document_chunks
            WHERE file_id IN ({placeholders})
              AND chunk_text LIKE ? ESCAPE '\\'
            ORDER BY chunk_index LIMIT ?
        """
        with self._connection_factory() as connection:
            rows = connection.execute(sql, (*file_ids, f"%{escaped}%", limit)).fetchall()
        results = []
        for row in rows:
            item = self._deserialize(row)
            item["rank"] = float(row["rank"])
            results.append(item)
        return results

    @staticmethod
    def _delete(connection: sqlite3.Connection, file_id: str) -> None:
        chunk_ids = [
            row[0]
            for row in connection.execute(
                "SELECT chunk_id FROM document_chunks WHERE file_id = ?", (file_id,)
            ).fetchall()
        ]
        for chunk_id in chunk_ids:
            connection.execute(
                "DELETE FROM document_chunks_fts WHERE chunk_id = ?", (chunk_id,)
            )
        connection.execute("DELETE FROM document_chunks WHERE file_id = ?", (file_id,))

    @staticmethod
    def _deserialize(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item
