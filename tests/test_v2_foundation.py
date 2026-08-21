"""V2.1 migration, stable file identity, repository, and locator tests."""

import sqlite3
from pathlib import Path

import pytest

from backend import database
from backend.migrations import Migration, run_migrations
from backend.repositories.file_repository import FileRepository
from backend.services.file_locator import (
    FileLocatorError,
    resolve_by_file_id,
    resolve_by_file_name,
)
from backend.tools import excel_utils


V1_FILE_INSERT = """
    INSERT INTO files (
        file_name, file_type, file_path, created_at, status, writable
    ) VALUES (?, ?, ?, ?, ?, ?)
"""


def _v1_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    database.create_base_schema(connection)
    connection.commit()
    return connection


def _insert_v1_file(
    connection: sqlite3.Connection,
    *,
    file_name: str,
    file_path: str,
) -> None:
    connection.execute(
        V1_FILE_INSERT,
        (file_name, "excel", file_path, "2026-01-01T00:00:00+00:00", "active", 0),
    )
    connection.commit()


def test_empty_database_migration_creates_history_and_v2_columns(tmp_path: Path) -> None:
    with _v1_connection(tmp_path / "empty.db") as connection:
        assert run_migrations(connection) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(files)").fetchall()
        }
        assert {
            "file_id",
            "source_type",
            "lifecycle_status",
            "parse_status",
            "queryable",
            "index_status",
            "updated_at",
            "deletable",
        } <= columns
        history = connection.execute(
            "SELECT version, name, applied_at FROM schema_migrations"
        ).fetchall()
        assert [(row[0], row[1]) for row in history] == [
            (1, "add_files_v2_foundation"),
            (2, "normalize_file_lifecycle"),
            (3, "create_excel_schema_tables"),
            (4, "add_field_semantic_mapping"),
            (5, "create_document_chunks"),
            (6, "create_runtime_tasks"),
            (7, "create_file_versions"),
            (8, "create_batches"),
            (9, "create_traces_and_context"),
            (10, "add_async_task_runtime"),
        ]
        assert history[0][2]
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10


def test_v1_database_is_migrated_in_place_with_stable_unique_ids(
    tmp_path: Path,
) -> None:
    with _v1_connection(tmp_path / "v1.db") as connection:
        _insert_v1_file(
            connection,
            file_name="固定文件.xlsx",
            file_path="data/固定文件.xlsx",
        )
        _insert_v1_file(
            connection,
            file_name="上传文件.xlsx",
            file_path="data/uploads/上传文件.xlsx",
        )

        run_migrations(connection)
        rows = connection.execute(
            """
            SELECT file_name, file_path, file_id, source_type, queryable, deletable
            FROM files ORDER BY id
            """
        ).fetchall()

        assert [row["file_name"] for row in rows] == ["固定文件.xlsx", "上传文件.xlsx"]
        assert [row["file_path"] for row in rows] == [
            "data/固定文件.xlsx",
            "data/uploads/上传文件.xlsx",
        ]
        assert all(row["file_id"] for row in rows)
        assert len({row["file_id"] for row in rows}) == 2
        assert (rows[0]["source_type"], rows[0]["queryable"], rows[0]["deletable"]) == (
            "system",
            1,
            0,
        )
        assert (rows[1]["source_type"], rows[1]["queryable"], rows[1]["deletable"]) == (
            "upload",
            1,
            1,
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE files SET file_id = ? WHERE id = ?",
                (rows[0]["file_id"], 2),
            )


def test_migration_is_idempotent_and_preserves_file_identity(tmp_path: Path) -> None:
    with _v1_connection(tmp_path / "idempotent.db") as connection:
        _insert_v1_file(
            connection,
            file_name="学生基本信息.xlsx",
            file_path="data/学生基本信息.xlsx",
        )
        assert run_migrations(connection) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        original = connection.execute(
            "SELECT file_name, file_id FROM files"
        ).fetchone()

        assert run_migrations(connection) == []
        current = connection.execute(
            "SELECT file_name, file_id FROM files"
        ).fetchone()
        assert tuple(current) == tuple(original)
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == 10


def test_failed_migration_rolls_back_all_changes(tmp_path: Path) -> None:
    with _v1_connection(tmp_path / "rollback.db") as connection:
        run_migrations(connection)

        def fail_after_schema_change(active: sqlite3.Connection) -> None:
            active.execute("CREATE TABLE must_be_rolled_back (id INTEGER PRIMARY KEY)")
            active.execute("INSERT INTO must_be_rolled_back (id) VALUES (?)", (1,))
            raise RuntimeError("forced migration failure")

        failing = Migration(11, "forced_failure", fail_after_schema_change)
        with pytest.raises(RuntimeError, match="forced migration failure"):
            run_migrations(connection, (failing,))

        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = ?",
            ("must_be_rolled_back",),
        ).fetchone() is None
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?", (11,)
        ).fetchone()[0] == 0
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10


def test_repository_keeps_file_id_when_metadata_is_renamed(tmp_path: Path) -> None:
    database_path = tmp_path / "repository.db"
    with _v1_connection(database_path) as connection:
        run_migrations(connection)

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        return connection

    repository = FileRepository(connect)
    repository.register(
        file_name="旧名称.xlsx",
        file_type="excel",
        file_path="data/uploads/旧名称.xlsx",
        created_at="2026-01-01T00:00:00+00:00",
        source_type="upload",
        queryable=False,
        deletable=True,
    )
    before = repository.get_by_name("旧名称.xlsx")
    assert before is not None

    assert repository.rename_record(
        file_id=before["file_id"],
        file_name="新名称.xlsx",
        file_path="data/uploads/新名称.xlsx",
        updated_at="2026-01-02T00:00:00+00:00",
    )
    after = repository.get_by_name("新名称.xlsx")
    assert after is not None
    assert after["file_id"] == before["file_id"]
    assert repository.get_by_name("旧名称.xlsx") is None


def test_v1_file_name_lookup_still_works_after_migration() -> None:
    record = database.get_file_record("学生基本信息.xlsx")

    assert record is not None
    assert record["file_name"] == "学生基本信息.xlsx"
    assert record["file_id"]
    assert record["source_type"] == "system"


def test_file_locator_resolves_system_and_upload_by_name_and_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    system_path = tmp_path / "定位系统.xlsx"
    system_path.write_bytes(b"system")
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    upload_path = upload_dir / "定位上传.docx"
    upload_path.write_bytes(b"upload")
    database.initialize_database()

    system_record = database.get_file_record("定位系统.xlsx")
    upload_record = database.get_file_record("定位上传.docx")
    assert system_record is not None and upload_record is not None
    assert resolve_by_file_name("定位系统.xlsx") == system_path.resolve()
    assert resolve_by_file_id(system_record["file_id"]) == system_path.resolve()
    assert resolve_by_file_name("定位上传.docx") == upload_path.resolve()
    assert resolve_by_file_id(upload_record["file_id"]) == upload_path.resolve()
    assert system_record["source_type"] == "system"
    assert system_record["deletable"] == 0
    assert upload_record["source_type"] == "upload"
    assert upload_record["deletable"] == 1


@pytest.mark.parametrize(
    "unsafe_name",
    ["../越界.xlsx", "folder/越界.xlsx", "..\\越界.xlsx", "/tmp/越界.xlsx"],
)
def test_file_locator_rejects_traversal_and_absolute_paths(unsafe_name: str) -> None:
    with pytest.raises(FileLocatorError, match="不能包含路径"):
        resolve_by_file_name(unsafe_name)


def test_file_locator_rejects_unsafe_registered_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(excel_utils, "DATA_DIR", tmp_path)
    database.register_file(
        file_name="危险记录.xlsx",
        file_type="excel",
        file_path="data/../危险记录.xlsx",
    )

    with pytest.raises(FileLocatorError, match="路径越界"):
        resolve_by_file_name("危险记录.xlsx")
