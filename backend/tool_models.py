"""Strict Pydantic argument models for registered Python tools."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmptyArguments(ToolArguments):
    pass


class StudentSearchArguments(ToolArguments):
    name_or_id: str = Field(min_length=1)


class StudentIdArguments(ToolArguments):
    student_id: str = Field(min_length=1)


class EntityIdArguments(ToolArguments):
    entity_id: str = Field(min_length=1)


class CompareStudentsArguments(ToolArguments):
    student_ids: list[str] = Field(min_length=2)

    @field_validator("student_ids")
    @classmethod
    def validate_student_ids(cls, values: list[str]) -> list[str]:
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("student_ids 必须包含有效学号")
        return [value.strip() for value in values]


class FileIdArguments(ToolArguments):
    file_id: str = Field(pattern=r"^[0-9a-fA-F]{32}$")


class DuplicateRecordsArguments(FileIdArguments):
    keys: list[str] = Field(min_length=1)

    @field_validator("keys")
    @classmethod
    def validate_keys(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("keys 不能包含空字段")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("keys 不能包含重复字段")
        return cleaned


class TableSchemaArguments(FileIdArguments):
    sheet_name: str | None = Field(default=None, min_length=1)


FilterOperator = Literal[
    "=", "!=", ">", ">=", "<", "<=", "contains", "is_null", "not_null"
]


class TableFilter(ToolArguments):
    field: str = Field(min_length=1)
    op: FilterOperator
    value: Any = None

    @model_validator(mode="after")
    def validate_value(self) -> "TableFilter":
        if self.op not in {"is_null", "not_null"} and self.value is None:
            raise ValueError(f"操作符 {self.op} 必须提供 value")
        return self


class TableOrder(ToolArguments):
    field: str = Field(min_length=1)
    direction: Literal["asc", "desc"] = "asc"


class QueryTableArguments(FileIdArguments):
    sheet: str = Field(min_length=1)
    filters: list[TableFilter] = Field(default_factory=list)
    select: list[str] = Field(min_length=1)
    order_by: TableOrder | list[TableOrder] | None = None
    limit: int | None = Field(default=None, ge=1, le=1000)

    @field_validator("select")
    @classmethod
    def validate_select(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("select 不能包含空字段")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("select 不能包含重复字段")
        return cleaned


AggregateOperation = Literal["count", "sum", "avg", "min", "max"]


class AggregateTableArguments(FileIdArguments):
    sheet: str = Field(min_length=1)
    operation: AggregateOperation
    field: str | None = Field(default=None, min_length=1)
    group_by: list[str] = Field(default_factory=list)
    filters: list[TableFilter] = Field(default_factory=list)

    @field_validator("group_by", mode="before")
    @classmethod
    def normalize_group_by(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value]
        return value

    @model_validator(mode="after")
    def validate_aggregation(self) -> "AggregateTableArguments":
        if self.operation != "count" and self.field is None:
            raise ValueError(f"{self.operation} 必须指定 field")
        cleaned_groups = [value.strip() for value in self.group_by]
        if any(not value for value in cleaned_groups):
            raise ValueError("group_by 不能包含空字段")
        if len(cleaned_groups) != len(set(cleaned_groups)):
            raise ValueError("group_by 不能包含重复字段")
        self.group_by = cleaned_groups
        return self
