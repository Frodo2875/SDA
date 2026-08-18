"""OpenAI-compatible chat-completions client for the Agent layer."""

import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class LLMConfigurationError(RuntimeError):
    """Raised when required LLM environment variables are absent."""


class LLMAPIError(RuntimeError):
    """Raised when the configured model API cannot return a valid response."""


class LLMClient:
    """Minimal client that only communicates with an OpenAI-compatible API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        load_dotenv(ENV_FILE)
        self.api_key = api_key or os.getenv("LLM_API_KEY", "").strip()
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "").strip()
        self.model = model or os.getenv("LLM_MODEL", "").strip()
        self.timeout = timeout

        missing = [
            name
            for name, value in (
                ("LLM_API_KEY", self.api_key),
                ("LLM_BASE_URL", self.base_url),
                ("LLM_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise LLMConfigurationError(f"缺少 LLM 配置：{', '.join(missing)}")

    def _chat_completions_url(self) -> str:
        base_url = self.base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    async def create_chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Request one non-streaming model response."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self._chat_completions_url(),
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMAPIError(f"模型 API 返回 HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMAPIError("无法连接模型 API 或响应格式无效") from exc

        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMAPIError("模型 API 响应缺少 choices[0].message") from exc
        if not isinstance(message, dict):
            raise LLMAPIError("模型 API 返回了无效消息")
        return message
