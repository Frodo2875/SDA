"""Replaceable embedding interface with a deterministic local default."""

import hashlib
import math
import re
from typing import Protocol


class EmbeddingProvider(Protocol):
    """Backend-neutral interface for query and document embeddings."""

    def embed_query(self, text: str) -> list[float]: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class LocalHashEmbeddingProvider:
    """Bounded local feature hashing; no model, network, or generated code."""

    def __init__(self, dimensions: int = 2048) -> None:
        if dimensions < 128:
            raise ValueError("embedding dimensions 不能小于 128")
        self.dimensions = dimensions

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


def get_default_embedding_provider() -> EmbeddingProvider:
    return LocalHashEmbeddingProvider()


def _tokens(text: str) -> list[str]:
    normalized = str(text).strip().casefold()
    words = re.findall(r"[a-z0-9]+", normalized)
    chinese = re.findall(r"[\u4e00-\u9fff]", normalized)
    chinese_bigrams = [
        "".join(chinese[index : index + 2])
        for index in range(max(0, len(chinese) - 1))
    ]
    return words + chinese + chinese_bigrams
