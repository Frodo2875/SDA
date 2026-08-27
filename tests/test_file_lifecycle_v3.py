"""V3.3 canonical document lifecycle state-machine tests."""

import json

from backend import database
from backend.repositories.file_repository import FileLifecycleStatus
from backend.services.file_lifecycle import (
    TRACE_SESSION_PREFIX,
    get_file_lifecycle,
    resume_file_lifecycle,
    transition_file_lifecycle,
)


def _uploaded_file(name: str) -> dict:
    database.register_file(
        file_name=name,
        file_type="pdf",
        file_path=f"data/uploads/{name}",
        lifecycle_status="uploaded",
        parse_status="pending",
        queryable=False,
    )
    record = database.get_file_record(name)
    assert record is not None
    return record


def test_v3_lifecycle_runs_the_complete_allowed_state_machine() -> None:
    record = _uploaded_file("完整状态机.pdf")
    states = [
        FileLifecycleStatus.DETECTING,
        FileLifecycleStatus.PARSING,
        FileLifecycleStatus.OCR_PROCESSING,
        FileLifecycleStatus.LAYOUT_PROCESSING,
        FileLifecycleStatus.INDEXING,
        FileLifecycleStatus.QUERYABLE,
    ]

    for state in states:
        result = transition_file_lifecycle(record["file_id"], state)
        assert result["ok"] is True
        assert result["data"]["status"] == state.value

    current = database.get_file_record_by_id(record["file_id"])
    assert current["status"] == FileLifecycleStatus.QUERYABLE.value
    assert current["lifecycle_status"] == "ready"
    assert current["parse_status"] == "parsed"
    assert current["index_status"] == "indexed"
    assert current["queryable"] == 1

    traces = database.get_session_trace_records(
        f"{TRACE_SESSION_PREFIX}{record['file_id']}"
    )
    assert len(traces) == len(states)
    assert all(trace["event_type"] == "file_lifecycle_transition" for trace in traces)
    assert all(trace["tool_name"] == "file_lifecycle" for trace in traces)
    assert [
        json.loads(trace["arguments_summary"])["to_status"] for trace in traces
    ] == [state.value for state in states]


def test_v3_lifecycle_rejects_queryable_to_uploaded() -> None:
    record = _uploaded_file("非法逆向转换.pdf")
    for state in (
        FileLifecycleStatus.DETECTING,
        FileLifecycleStatus.PARSING,
        FileLifecycleStatus.QUERYABLE,
    ):
        assert transition_file_lifecycle(record["file_id"], state)["ok"] is True

    rejected = transition_file_lifecycle(
        record["file_id"], FileLifecycleStatus.UPLOADED
    )

    assert rejected["ok"] is False
    assert rejected["error_code"] == "INVALID_FILE_STATE"
    assert get_file_lifecycle(record["file_id"])["data"]["status"] == "QUERYABLE"


def test_v3_failure_persists_error_and_resumes_from_checkpoint() -> None:
    record = _uploaded_file("失败恢复.pdf")
    assert transition_file_lifecycle(record["file_id"], "DETECTING")["ok"] is True
    assert transition_file_lifecycle(record["file_id"], "PARSING")["ok"] is True

    failed = transition_file_lifecycle(
        record["file_id"],
        "FAILED",
        error_code="FILE_PARSE_ERROR",
        error_message="模拟解析失败",
    )

    assert failed["ok"] is True
    persisted = get_file_lifecycle(record["file_id"])
    assert persisted["data"]["status"] == "FAILED"
    assert persisted["data"]["error"]["error_code"] == "FILE_PARSE_ERROR"
    assert persisted["data"]["error"]["message"] == "模拟解析失败"
    assert persisted["data"]["error"]["resume_status"] == "PARSING"

    database.initialize_database()
    resumed = resume_file_lifecycle(record["file_id"])

    assert resumed["ok"] is True
    assert resumed["data"]["status"] == "PARSING"
    completed = transition_file_lifecycle(record["file_id"], "QUERYABLE")
    assert completed["ok"] is True
    assert get_file_lifecycle(record["file_id"])["data"] == {
        "file_id": record["file_id"],
        "status": "QUERYABLE",
        "error": None,
        "lifecycle_status": "ready",
        "parse_status": "parsed",
        "index_status": "not_required",
        "queryable": True,
    }


def test_f10_reindexing_state_rejects_illegal_reverse_transition() -> None:
    record = _uploaded_file("重新索引非法转换.pdf")
    for state in (
        FileLifecycleStatus.DETECTING,
        FileLifecycleStatus.PARSING,
        FileLifecycleStatus.QUERYABLE,
        FileLifecycleStatus.REINDEXING,
    ):
        assert transition_file_lifecycle(record["file_id"], state)["ok"] is True

    rejected = transition_file_lifecycle(record["file_id"], "UPLOADED")

    assert rejected["ok"] is False
    assert rejected["error_code"] == "INVALID_FILE_STATE"
    assert get_file_lifecycle(record["file_id"])["data"]["status"] == "REINDEXING"
