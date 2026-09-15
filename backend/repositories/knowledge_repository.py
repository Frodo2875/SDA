"""Management metadata only; documents and their indexes remain canonical."""

import sqlite3
from collections.abc import Callable
from typing import Any
from uuid import uuid4


class KnowledgeRepository:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self.connection_factory = connection_factory

    def create(self, name: str, description: str, timestamp: str) -> str:
        identifier = uuid4().hex
        with self.connection_factory() as connection:
            connection.execute("INSERT INTO knowledge_bases VALUES (?, ?, ?, ?, ?)",
                               (identifier, name, description, timestamp, timestamp))
        return identifier

    def list(self) -> list[dict[str, Any]]:
        with self.connection_factory() as connection:
            records = [dict(row) for row in connection.execute("SELECT * FROM knowledge_bases ORDER BY created_at DESC, knowledge_base_id")]
            files = [dict(row) for row in connection.execute("""
                SELECT k.knowledge_base_id, k.attached_at, f.file_id, f.updated_at,
                       f.lifecycle_status, f.index_status, f.queryable
                FROM knowledge_base_files k JOIN files f ON f.file_id = k.file_id
            """)]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in files:
            grouped.setdefault(row["knowledge_base_id"], []).append(row)
        for record in records:
            members = grouped.get(record["knowledge_base_id"], [])
            active = [row for row in members if row["lifecycle_status"].lower() != "deleted"]
            record["file_count"] = len(active)
            record["updated_at"] = max([record["updated_at"], *[max(row["updated_at"] or "", row["attached_at"]) for row in members]])
            failed = any(row["lifecycle_status"].lower() in {"failed", "cleanup_failed"} or row["index_status"] == "failed" for row in active)
            ready = all(row["lifecycle_status"].lower() in {"ready", "queryable"}
                        and (bool(row["queryable"]) or row["index_status"] == "indexed") for row in active)
            record["status"] = "Empty" if not active else "Failed" if failed else "Ready" if ready else "Indexing"
        return records

    def get(self, identifier: str) -> dict[str, Any] | None:
        return next((row for row in self.list() if row["knowledge_base_id"] == identifier), None)

    def file_ids(self, identifier: str) -> set[str]:
        with self.connection_factory() as connection:
            return {row[0] for row in connection.execute("SELECT file_id FROM knowledge_base_files WHERE knowledge_base_id = ?", (identifier,))}

    def attach(self, identifier: str, file_id: str, timestamp: str) -> None:
        with self.connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = connection.execute("SELECT lifecycle_status FROM files WHERE file_id = ?", (file_id,)).fetchone()
            if record is None or record[0].lower() in {"deleted", "cleanup_failed"}:
                raise ValueError("文件不存在或已删除")
            changed = connection.execute("INSERT OR IGNORE INTO knowledge_base_files VALUES (?, ?, ?)", (identifier, file_id, timestamp)).rowcount
            if changed:
                connection.execute("UPDATE knowledge_bases SET updated_at = ? WHERE knowledge_base_id = ?", (timestamp, identifier))
