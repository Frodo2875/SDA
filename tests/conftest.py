"""Pytest configuration for importing the project package from any pytest entry point."""

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test's persistence isolated from data/app.db."""
    from backend import database

    test_database = tmp_path / "app.db"
    monkeypatch.setattr(database, "DB_PATH", test_database)
    database.initialize_database()
    return test_database


@pytest.fixture(autouse=True)
def isolated_web_search_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent a developer's local API key from making tests access Tavily."""
    from backend.services.search_provider import UnavailableSearchProvider
    from backend.services.web_retrieval import WEB_RETRIEVAL_SERVICE

    monkeypatch.setattr(
        WEB_RETRIEVAL_SERVICE, "search_provider", UnavailableSearchProvider()
    )
