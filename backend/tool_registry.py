"""Single-source registration for allow-listed Agent tools."""

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from backend.tool_models import (
    AggregateTableArguments,
    CompareStudentsArguments,
    DuplicateRecordsArguments,
    EmptyArguments,
    EntityIdArguments,
    FileIdArguments,
    QueryTableArguments,
    RetrieveDocumentArguments,
    ScholarshipEvaluationArguments,
    StudentIdArguments,
    StudentSearchArguments,
    TableSchemaArguments,
)
from backend.tools.analysis_tools import compare_students, get_top_three_students
from backend.tools.data_quality_tools import (
    find_cross_file_conflicts,
    find_duplicate_records,
    validate_document,
    validate_student_data,
)
from backend.tools.file_tools import list_files
from backend.tools.schema_tools import get_table_schema, inspect_excel
from backend.tools.student_tools import (
    get_student_info,
    get_student_research,
    get_student_scores,
    search_student,
)
from backend.tools.table_tools import aggregate_table, query_table
from backend.tools.document_tools import retrieve_document
from backend.tools.hybrid_tools import evaluate_scholarship_eligibility


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
            "列出知识库中受支持的 Excel、Word 和普通文本 PDF 文件。",
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
            retryable=True,
        ),
        _spec(
            "aggregate_table",
            "由 Python 对已解析 Excel Sheet 执行 count、sum、avg、min 或 max。",
            AggregateTableArguments,
            aggregate_table,
        ),
        _spec(
            "find_cross_file_conflicts",
            "按受控关联规则检查一个实体的跨文件冲突，不自动选择真实值。",
            EntityIdArguments,
            find_cross_file_conflicts,
        ),
        _spec(
            "validate_document",
            "由 Python 校验一个已解析 Excel 的字段、空值、重复、类型、范围和联系方式质量。",
            FileIdArguments,
            validate_document,
        ),
        _spec(
            "find_duplicate_records",
            "按真实 Schema 字段查找一个 Excel 内的重复键记录。",
            DuplicateRecordsArguments,
            find_duplicate_records,
        ),
        _spec(
            "validate_student_data",
            "按学号验证学生跨文档数据质量、关联可靠性和属性冲突。",
            StudentIdArguments,
            validate_student_data,
        ),
        _spec(
            "retrieve_document",
            "从已索引 PDF 或 Word 中检索有限条材料依据，并返回可引用 Evidence。",
            RetrieveDocumentArguments,
            retrieve_document,
            retryable=True,
        ),
        _spec(
            "evaluate_scholarship_eligibility",
            "只使用本轮已查询的成绩、科研与规则 Evidence，由 Python 判断奖学金条件；参数中不接受阈值或学生数值。",
            ScholarshipEvaluationArguments,
            evaluate_scholarship_eligibility,
        ),
    ]
)
