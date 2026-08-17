"""Persistence repositories for V2-compatible domain records."""

from backend.repositories.file_repository import FileRepository
from backend.repositories.schema_repository import SchemaRepository

__all__ = ["FileRepository", "SchemaRepository"]
