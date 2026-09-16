"""Read-only system status, scoped counts and secret-free projection."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from frontend import api_client
from frontend.system_dashboard import collect_snapshot

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"


@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch) -> list:
    calls = []
    def request(method: str, path: str, **kwargs: object) -> dict:
        calls.append((method, path))
        return {"status": "ok", "version": "0.1.0", "api_key": "sk-SECRET", "Authorization": "Bearer PRIVATE"}
    monkeypatch.setattr(api_client, "request", request)
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [{"file_name": "a.md", "lifecycle_status": "parsing"},
                        {"file_name": "b.md", "lifecycle_status": "failed"}, {"file_name": "c.md", "index_status": "indexed"}])
    monkeypatch.setattr(api_client, "list_knowledge_bases", lambda: [{"name": "secret-name"}])
    monkeypatch.setattr(api_client, "list_tasks", lambda _: [{"task": {"task_id": "r1", "task_type": "async_research"}}])
    monkeypatch.setattr(api_client, "get_research_task", lambda _: {"result": {"reports": {"report1": {}}, "unified_evidence": [{"evidence_id": "ev1"}, {"evidence_id": "ev1"}]}})
    return calls


def test_snapshot_counts_deduplicate_and_exclude_secrets(sources: list) -> None:
    snapshot = collect_snapshot({"first", "second"})
    assert snapshot["backend"] == "Online"
    assert snapshot["counts"] == {"文件": 3, "知识库": 1, "研究任务": 1, "报告": 1, "Evidence": 1}
    assert (snapshot["processing"], snapshot["failed"], snapshot["indexed"]) == (1, 1, 1)
    assert "SECRET" not in str(snapshot) and "PRIVATE" not in str(snapshot) and "secret-name" not in str(snapshot)
    assert sources == [("GET", "/health")]


@pytest.mark.parametrize("page", ["dashboard", "usage", "settings"])
def test_pages_refresh_health_and_scoped_counts(sources: list, page: str) -> None:
    ui = AppTest.from_file(APP).run(timeout=10)
    ui.button(key=f"nav-{page}").click().run()
    assert not ui.exception
    assert not sources
    ui.button(key="system-refresh").click().run()
    assert not ui.exception
    assert any(item.value == "在线" for item in ui.text)
    assert any(item.value == "引用资料：1" for item in ui.text)
    assert any("暂未检测" in item.value for item in ui.text)
    assert any("暂不提供单独的连接检测" in item.value for item in ui.caption)
    assert "SECRET" not in str(ui.session_state.system_snapshot)


def test_failures_use_static_messages_and_unknown_counts(sources: list, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("Authorization: Bearer TOP-SECRET api_key=PRIVATE")
    for method in ["request", "list_files", "list_knowledge_bases", "list_tasks"]:
        monkeypatch.setattr(api_client, method, fail)
    snapshot = collect_snapshot({"session"})
    assert snapshot["backend"] == "Unavailable"
    assert all(value is None for value in snapshot["counts"].values())
    assert len(snapshot["errors"]) == 4
    assert "TOP-SECRET" not in str(snapshot) and "PRIVATE" not in str(snapshot)


def test_partial_task_failure_does_not_claim_complete_totals(sources: list, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_: str) -> dict:
        raise RuntimeError("failed")
    monkeypatch.setattr(api_client, "get_research_task", fail)
    snapshot = collect_snapshot({"session"})
    assert snapshot["counts"]["文件"] == 3
    assert snapshot["counts"]["研究任务"] is None
    assert snapshot["counts"]["报告"] is None


@pytest.mark.parametrize("version", ["sk-credential", "Bearer abc", "https://host?token=abc", {"api_key": "abc"}])
def test_health_version_is_strictly_projected(sources: list, monkeypatch: pytest.MonkeyPatch, version: object) -> None:
    monkeypatch.setattr(api_client, "request", lambda *args, **kwargs: {"status": "ok", "version": version})
    assert collect_snapshot(set())["version"] == "未提供"


def test_settings_has_no_configuration_inputs(sources: list, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "DO-NOT-DISPLAY")
    monkeypatch.setenv("LLM_MODEL", "ALSO-NOT-AUTHORITATIVE")
    ui = AppTest.from_file(APP).run(timeout=10)
    ui.button(key="nav-settings").click().run()
    ui.button(key="system-refresh").click().run()
    assert not ui.exception and not ui.text_input
    output = " ".join(item.value for kind in [ui.text, ui.caption, ui.markdown, ui.info, ui.warning] for item in kind)
    assert "DO-NOT-DISPLAY" not in output and "ALSO-NOT-AUTHORITATIVE" not in output


def test_empty_success_is_zero(sources: list, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client, "list_files", lambda **kwargs: [])
    monkeypatch.setattr(api_client, "list_knowledge_bases", lambda: [])
    monkeypatch.setattr(api_client, "list_tasks", lambda _: [])
    snapshot = collect_snapshot({"session"})
    assert all(value == 0 for value in snapshot["counts"].values())
