"""SQLite persistence helpers for durable per-page OCR results."""

import json
import sqlite3
from collections.abc import Callable
from typing import Any


ConnectionFactory = Callable[[], sqlite3.Connection]


class OCRRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def get_for_file(self, file_id: str) -> list[dict[str, Any]]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM document_ocr_pages WHERE file_id = ? ORDER BY page_no",
                (file_id,),
            ).fetchall()
        return [self._deserialize(row) for row in rows]

    def get_page(self, file_id: str, page_no: int) -> dict[str, Any] | None:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM document_ocr_pages WHERE file_id = ? AND page_no = ?",
                (file_id, int(page_no)),
            ).fetchone()
        return self._deserialize(row) if row is not None else None

    def replace_for_file(self, file_id: str, pages: list[dict[str, Any]]) -> None:
        with self._connection_factory() as connection:
            self.replace_in_transaction(connection, file_id, pages)

    @staticmethod
    def replace_in_transaction(
        connection: sqlite3.Connection,
        file_id: str,
        pages: list[dict[str, Any]],
    ) -> None:
        connection.execute("DELETE FROM document_ocr_pages WHERE file_id = ?", (file_id,))
        for page in pages:
            connection.execute(
                """
                INSERT INTO document_ocr_pages (
                    file_id, page_no, text, bbox_json, confidence, status,
                    error, source_type, blocks_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    int(page["page_no"]),
                    str(page.get("text") or ""),
                    json.dumps(page.get("bbox"), ensure_ascii=False),
                    page.get("confidence"),
                    page["status"],
                    page.get("error"),
                    page["source_type"],
                    json.dumps(page.get("blocks") or [], ensure_ascii=False),
                    page["updated_at"],
                ),
            )

    @staticmethod
    def _deserialize(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["bbox"] = json.loads(item.pop("bbox_json") or "null")
        item["blocks"] = json.loads(item.pop("blocks_json") or "[]")
        return item
