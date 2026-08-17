"""Single-source registration for allow-listed Agent tools."""

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from backend.tool_models import (
    AggregateTableArguments,
    CompareStudentsArguments,
    EmptyArguments,
    FileIdArguments,
    QueryTableArguments,
    StudentIdArguments,
    StudentSearchArguments,
    TableSchemaArguments,
)
from backend.tools.analysis_tools import compare_students, get_top_three_students
from backend.tools.file_tools import list_files
from backend.tools.schema_tools import get_table_schema, inspect_excel
from backend.tools.student_tools import (
    get_student_info,
    get_student_research,
    get_student_scores,
    search_student,
)
from backend.tools.table_tools import aggregate_table, query_table


ToolHandler = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: ToolHandler
    read_only: bool
    retryable: bool
    requires_confirmation: bool

    def llm_definition(self) -> dict[str, Any]:
        parameters = self.arguments_model.model_json_schema()
        parameters.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


class ToolRegistry:
    """Validate unique registration and derive all compatibility views."""

    def __init__(self, specs: list[ToolSpec]) -> None:
        names = [spec.name for spec in specs]
        if len(names) != len(set(names)):
            raise ValueError("Tool name 不能重复注册")
        self._specs = {spec.name: spec for spec in specs}

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def definitions(self) -> list[dict[str, Any]]:
        return [spec.llm_definition() for spec in self._specs.values()]

    def handlers(self) -> dict[str, ToolHandler]:
        return {name: spec.handler for name, spec in self._specs.items()}

    def legacy_argument_types(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                field_name: field.annotation
                for field_name, field in spec.arguments_model.model_fields.items()
            }
            for name, spec in self._specs.items()
        }


def _spec(
    name: str,
    description: str,
    arguments_model: type[BaseModel],
    handler: ToolHandler,
    *,
    retryable: bool = False,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        arguments_model=arguments_model,
        handler=handler,
        read_only=True,
        retryable=retryable,
        requires_confirmation=False,
    )


TOOL_REGISTRY = ToolRegistry(
    [
        _spec(
            "get_top_three_students",
            "由 Python 读取固定成绩材料、重新计算平均分并返回最高三名。",
            EmptyArguments,
            get_top_three_students,
        ),
        _spec(
            "list_files",
            "列出知识库中受支持的 Excel 和 Word 文件。",
            EmptyArguments,
            list_files,
            retryable=True,
        ),
        _spec(
            "search_student",
            "按完整姓名或学号搜索学生，并识别重名或不存在。",
            StudentSearchArguments,
            search_student,
        ),
        _spec(
            "get_student_info",
            "通过已确认学号查询学生基本信息。",
            StudentIdArguments,
            get_student_info,
        ),
        _spec(
            "get_student_scores",
            "通过学号查询成绩、Python 计算平均分和专业排名。",
            StudentIdArguments,
            get_student_scores,
        ),
        _spec(
            "get_student_research",
            "通过学号查询科研记录，并保留 no_record 语义。",
            StudentIdArguments,
            get_student_research,
        ),
        _spec(
            "compare_students",
            "由 Python 比较至少两名学生的成绩、排名和科研数量。",
            CompareStudentsArguments,
            compare_students,
        ),
        _spec(
            "inspect_excel",
            "确定性识别一个已登记 Excel 的逐 Sheet Schema。",
            FileIdArguments,
            inspect_excel,
            retryable=True,
        ),
        _spec(
            "get_table_schema",
            "读取一个 Excel 已保存的真实逐 Sheet Schema。",
            TableSchemaArguments,
            get_table_schema,
        ),
        _spec(
            "query_table",
            "用白名单过滤、字段选择、排序和 limit 查询已解析 Excel Sheet。",
            QueryTableArguments,
            query_table,
        ),
        _spec(
            "aggregate_table",
            "由 Python 对已解析 Excel Sheet 执行 count、sum、avg、min 或 max。",
            AggregateTableArguments,
            aggregate_table,
        ),
    ]
)
