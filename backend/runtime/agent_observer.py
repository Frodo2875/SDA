"""Optional observation/checkpoint port; the Agent remains the only executor."""

from typing import Any, Protocol


class AgentObserver(Protocol):
    @property
    def task_id(self) -> str: ...

    def load(self) -> dict[str, Any]: ...

    def save(self, **values: Any) -> None: ...

    def stage(self, name: str) -> None: ...
