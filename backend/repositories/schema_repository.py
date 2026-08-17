"""SQLite persistence for discovered per-sheet table schemas."""

import sqlite3
from collections.abc import Callable
from typing import Any
from uuid import uuid4


ConnectionFactory = Callable[[], sqlite3.Connection]


class SchemaRepository:
    """Replace and read all discovered schemas for one stable file ID."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def replace_for_file(
        self,
        *,
        file_id: str,
        schemas: list[dict[str, Any]],
        created_at: str,
    ) -> None:
        """Atomically replace prior discovery output for one file."""
        with self._connection_factory() as connection:
            connection.execute("DELETE FROM table_schemas WHERE file_id = ?", (file_id,))
            for schema in schemas:
                schema_id = uuid4().hex
                connection.execute(
                    """
                    INSERT INTO table_schemas (
                        schema_id, file_id, sheet_name, header_row, data_start_row,
                        row_count, column_count, detection_status, confidence,
                        detection_message, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        schema_id,
                        file_id,
                        schema["sheet_name"],
                        schema.get("header_row"),
                        schema.get("data_start_row"),
                        schema["row_count"],
                        schema["column_count"],
                        schema["detection_status"],
                        schema["confidence"],
                        schema.get("detection_message"),
                        created_at,
                    ),
                )
                for field in schema.get("fields", []):
                    connection.execute(
                        """
                        INSERT INTO schema_fields (
                            field_id, schema_id, source_name, source_index,
                            inferred_type, nullable, null_count, null_ratio,
                            unique_count, semantic_type, confidence, sensitive
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            uuid4().hex,
                            schema_id,
                            field["source_name"],
                            field["source_index"],
                            field["inferred_type"],
                            int(field["nullable"]),
                            field["null_count"],
                            field["null_ratio"],
                            field["unique_count"],
                            field["semantic_type"],
                            field["confidence"],
                            int(field["sensitive"]),
                        ),
                    )

    def get_for_file(
        self,
        *,
        file_id: str,
        sheet_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return stored schemas and their fields without re-reading Excel."""
        with self._connection_factory() as connection:
            if sheet_name is None:
                schema_rows = connection.execute(
                    """
                    SELECT * FROM table_schemas
                    WHERE file_id = ? ORDER BY rowid
                    """,
                    (file_id,),
                ).fetchall()
            else:
                schema_rows = connection.execute(
                    """
                    SELECT * FROM table_schemas
                    WHERE file_id = ? AND sheet_name = ? ORDER BY rowid
                    """,
                    (file_id, sheet_name),
                ).fetchall()

            results = []
            for schema_row in schema_rows:
                schema = dict(schema_row)
                field_rows = connection.execute(
                    """
                    SELECT * FROM schema_fields
                    WHERE schema_id = ? ORDER BY source_index
                    """,
                    (schema["schema_id"],),
                ).fetchall()
                schema["fields"] = [dict(row) for row in field_rows]
                results.append(schema)
        return results

    def delete_for_file(self, file_id: str) -> None:
        """Remove stale schema output for a file that cannot be parsed."""
        with self._connection_factory() as connection:
            connection.execute("DELETE FROM table_schemas WHERE file_id = ?", (file_id,))
