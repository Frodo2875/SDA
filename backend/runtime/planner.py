"""Deterministic, V2-compatible plans for bounded complex workflows."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class StepType(str, Enum):
    """Stable V3 workflow step categories."""

    QUERY = "QUERY"
    ANALYSIS = "ANALYSIS"
    GENERATE = "GENERATE"
    CONFIRM = "CONFIRM"
    WRITE = "WRITE"


QUERY_TOOLS = {
    "search_student", "get_student_info", "get_student_scores",
    "get_student_research", "get_top_three_students", "list_files",
    "inspect_excel", "get_table_schema", "query_table", "retrieve_document",
}


@dataclass(frozen=True)
class PlannedStep:
    """One directly traceable step; first four fields retain the V2 constructor."""

    sequence: int
    step_name: str
    step_type: StepType | str
    tool_name: str | None = None
    step_id: str = field(default_factory=lambda: uuid4().hex)
    input: dict[str, Any] = field(default_factory=dict)
    expected_output: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_type", _normalize_step_type(self.step_type, self.tool_name))
        object.__setattr__(self, "input", dict(self.input))

    @property
    def description(self) -> str:
        return self.step_name

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "sequence": self.sequence,
            "step_type": self.step_type.value,
            "tool_name": self.tool_name,
            "description": self.description,
            "input": dict(self.input),
            "expected_output": self.expected_output,
        }


@dataclass(frozen=True)
class TaskPlan:
    task_type: str
    steps: tuple[PlannedStep, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"task_type": self.task_type, "steps": [step.as_dict() for step in self.steps]}


def create_plan(user_message: str) -> TaskPlan | None:
    """Return a complete plan for complex tasks and keep simple V2 queries light."""
    scholarship = _is_scholarship_task(user_message)
    write = _is_write_task(user_message)
    if scholarship and write:
        return _combined_scholarship_write_plan()
    if scholarship:
        return _scholarship_plan()
    if write:
        return _word_write_plan()
    return None


def _scholarship_plan() -> TaskPlan:
    return TaskPlan(
        task_type="scholarship_evaluation",
        steps=(
            _step(1, "确认学生身份", StepType.QUERY, "search_student", "唯一学生身份"),
            _step(2, "查询学生成绩", StepType.QUERY, "query_table", "成绩字段 Evidence"),
            _step(3, "查询科研成果", StepType.QUERY, "query_table", "科研字段 Evidence"),
            _step(4, "检索奖学金评审办法", StepType.QUERY, "retrieve_document", "规则 Evidence"),
            _step(5, "执行奖学金规则判断", StepType.ANALYSIS, "evaluate_scholarship_eligibility", "Python 规则判断结果"),
            _step(6, "生成有证据的解释", StepType.GENERATE, "generate_content", "有证据的最终回答"),
        ),
    )


def _word_write_plan() -> TaskPlan:
    return TaskPlan(
        task_type="confirmed_word_write",
        steps=(
            _step(1, "确认学生身份", StepType.QUERY, "get_student_info", "学生基本信息"),
            _step(2, "查询学生成绩", StepType.QUERY, "get_student_scores", "学生成绩"),
            _step(3, "查询科研成果", StepType.QUERY, "get_student_research", "学生科研记录"),
            _step(4, "生成待写入内容", StepType.GENERATE, "generate_content", "待写入 Word 正文"),
            _step(5, "生成 Word Diff 预览", StepType.GENERATE, "preview_word_diff", "冻结的 Diff 预览"),
            _step(6, "等待用户确认", StepType.CONFIRM, "confirm_action", "用户确认或取消"),
            _step(7, "执行确认写入", StepType.WRITE, "write_word", "新 Word 版本"),
        ),
    )


def _combined_scholarship_write_plan() -> TaskPlan:
    """Cover every Tool required by both evaluation and safe-write policies."""
    return TaskPlan(
        task_type="scholarship_evaluation_and_word_write",
        steps=(
            _step(1, "确认学生身份", StepType.QUERY, "search_student", "唯一学生身份"),
            _step(2, "读取学生基本信息", StepType.QUERY, "get_student_info", "学生基本信息"),
            _step(3, "读取学生成绩", StepType.QUERY, "get_student_scores", "学生成绩"),
            _step(4, "读取学生科研", StepType.QUERY, "get_student_research", "学生科研记录"),
            _step(5, "查询学生成绩", StepType.QUERY, "query_table", "成绩字段 Evidence"),
            _step(6, "查询科研成果", StepType.QUERY, "query_table", "科研字段 Evidence"),
            _step(7, "检索奖学金评审办法", StepType.QUERY, "retrieve_document", "规则 Evidence"),
            _step(8, "执行奖学金规则判断", StepType.ANALYSIS, "evaluate_scholarship_eligibility", "Python 规则判断结果"),
            _step(9, "生成待写入内容", StepType.GENERATE, "generate_content", "待写入 Word 正文"),
            _step(10, "生成 Word Diff 预览", StepType.GENERATE, "preview_word_diff", "冻结的 Diff 预览"),
            _step(11, "等待用户确认", StepType.CONFIRM, "confirm_action", "用户确认或取消"),
            _step(12, "执行确认写入", StepType.WRITE, "write_word", "新 Word 版本"),
        ),
    )


def _step(
    sequence: int,
    description: str,
    step_type: StepType,
    tool_name: str | None,
    expected_output: str,
) -> PlannedStep:
    return PlannedStep(
        sequence=sequence,
        step_name=description,
        step_type=step_type,
        tool_name=tool_name,
        input={"source": "user_message_or_previous_steps"},
        expected_output=expected_output,
    )


def _normalize_step_type(value: StepType | str, tool_name: str | None) -> StepType:
    if isinstance(value, StepType):
        return value
    normalized = str(value).strip().upper()
    if normalized in StepType.__members__:
        return StepType[normalized]
    legacy = str(value).strip().lower()
    if legacy == "generation":
        return StepType.GENERATE
    if legacy == "confirmation":
        return StepType.CONFIRM
    if legacy == "side_effect":
        return StepType.WRITE
    if legacy == "tool":
        return StepType.QUERY if tool_name in QUERY_TOOLS else StepType.ANALYSIS
    raise ValueError(f"不支持的 Step 类型：{value}")


def _is_scholarship_task(message: str) -> bool:
    return "奖学金" in message and any(
        word in message for word in ("判断", "满足", "符合", "条件", "资格")
    )


def _is_write_task(message: str) -> bool:
    return ".docx" in message.casefold() and any(
        word in message for word in ("写入", "写进")
    )
