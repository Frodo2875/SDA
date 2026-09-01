"""Agent-facing read-only interface for controlled public Web retrieval."""

from backend.services.web_retrieval import WEB_RETRIEVAL_SERVICE


def retrieve_web(
    query: str | None = None,
    url: str | None = None,
    top_k: int = 5,
    language: str | None = None,
    region: str | None = None,
) -> dict:
    return WEB_RETRIEVAL_SERVICE.retrieve(
        query=query,
        url=url,
        top_k=top_k,
        language=language,
        region=region,
    )


__all__ = ["retrieve_web"]
