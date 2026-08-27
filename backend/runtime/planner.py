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


class NodeType(str, Enum):
    """Execution semantics layered on top of the V2-compatible Step model."""

    TOOL = "tool"
    CONDITION = "condition"
    APPROVAL = "approval"
    AGGREGATE = "aggregate"


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
    node_type: NodeType | str = NodeType.TOOL
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    input_ref: dict[str, str] = field(default_factory=dict)
    output_ref: str | None = None
    condition: dict[str, Any] | None = None
    run_if: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_type", _normalize_step_type(self.step_type, self.tool_name))
        object.__setattr__(self, "input", dict(self.input))
        object.__setattr__(self, "node_type", NodeType(self.node_type))
        object.__setattr__(self, "depends_on", tuple(self.depends_on))
        object.__setattr__(self, "input_ref", dict(self.input_ref))
        object.__setattr__(self, "condition", dict(self.condition) if self.condition else None)
        object.__setattr__(self, "run_if", dict(self.run_if))

    @property
    def node_id(self) -> str:
        return self.step_id

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
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "depends_on": list(self.depends_on),
            "input_ref": dict(self.input_ref),
            "output_ref": self.output_ref,
            "condition": dict(self.condition) if self.condition else None,
            "run_if": dict(self.run_if),
        }


@dataclass(frozen=True)
class TaskPlan:
    task_type: str
    steps: tuple[PlannedStep, ...]
    workflow_id: str = field(default_factory=lambda: uuid4().hex)

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "task_type": self.task_type,
            "steps": [
                {**step.as_dict(), "workflow_id": self.workflow_id}
                for step in self.steps
            ],
        }


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
    identity = uuid4().hex
    scores = uuid4().hex
    research = uuid4().hex
    rules = uuid4().hex
    analysis = uuid4().hex
    return TaskPlan(
        task_type="scholarship_evaluation",
        steps=(
            _step(1, "确认学生身份", StepType.QUERY, "search_student", "唯一学生身份", step_id=identity),
            _step(2, "查询学生成绩", StepType.QUERY, "query_table", "成绩字段 Evidence", step_id=scores, depends_on=(identity,)),
            _step(3, "查询科研成果", StepType.QUERY, "query_table", "科研字段 Evidence", step_id=research, depends_on=(identity,)),
            _step(4, "检索奖学金评审办法", StepType.QUERY, "retrieve_document", "规则 Evidence", step_id=rules),
            _step(5, "执行奖学金规则判断", StepType.ANALYSIS, "evaluate_scholarship_eligibility", "Python 规则判断结果", step_id=analysis, depends_on=(scores, research, rules)),
            _step(6, "生成有证据的解释", StepType.GENERATE, "generate_content", "有证据的最终回答", depends_on=(analysis,)),
        ),
    )


def _word_write_plan() -> TaskPlan:
    ids = [uuid4().hex for _ in range(7)]
    return TaskPlan(
        task_type="confirmed_word_write",
        steps=(
            _step(1, "确认学生身份", StepType.QUERY, "get_student_info", "学生基本信息", step_id=ids[0]),
            _step(2, "查询学生成绩", StepType.QUERY, "get_student_scores", "学生成绩", step_id=ids[1], depends_on=(ids[0],)),
            _step(3, "查询科研成果", StepType.QUERY, "get_student_research", "学生科研记录", step_id=ids[2], depends_on=(ids[0],)),
            _step(4, "生成待写入内容", StepType.GENERATE, "generate_content", "待写入 Word 正文", step_id=ids[3], depends_on=(ids[1], ids[2])),
            _step(5, "生成 Word Diff 预览", StepType.GENERATE, "preview_word_diff", "冻结的 Diff 预览", step_id=ids[4], depends_on=(ids[3],)),
            _step(6, "等待用户确认", StepType.CONFIRM, "confirm_action", "用户确认或取消", step_id=ids[5], depends_on=(ids[4],), node_type=NodeType.APPROVAL),
            _step(7, "执行确认写入", StepType.WRITE, "write_word", "新 Word 版本", step_id=ids[6], depends_on=(ids[5],)),
        ),
    )


def _combined_scholarship_write_plan() -> TaskPlan:
    """Cover every Tool required by both evaluation and safe-write policies."""
    ids = [uuid4().hex for _ in range(12)]
    return TaskPlan(
        task_type="scholarship_evaluation_and_word_write",
        steps=(
            _step(1, "确认学生身份", StepType.QUERY, "search_student", "唯一学生身份", step_id=ids[0]),
            _step(2, "读取学生基本信息", StepType.QUERY, "get_student_info", "学生基本信息", step_id=ids[1], depends_on=(ids[0],)),
            _step(3, "读取学生成绩", StepType.QUERY, "get_student_scores", "学生成绩", step_id=ids[2], depends_on=(ids[1],)),
            _step(4, "读取学生科研", StepType.QUERY, "get_student_research", "学生科研记录", step_id=ids[3], depends_on=(ids[1],)),
            _step(5, "查询学生成绩", StepType.QUERY, "query_table", "成绩字段 Evidence", step_id=ids[4], depends_on=(ids[0],)),
            _step(6, "查询科研成果", StepType.QUERY, "query_table", "科研字段 Evidence", step_id=ids[5], depends_on=(ids[0],)),
            _step(7, "检索奖学金评审办法", StepType.QUERY, "retrieve_document", "规则 Evidence", step_id=ids[6]),
            _step(8, "执行奖学金规则判断", StepType.ANALYSIS, "evaluate_scholarship_eligibility", "Python 规则判断结果", step_id=ids[7], depends_on=(ids[4], ids[5], ids[6])),
            _step(9, "生成待写入内容", StepType.GENERATE, "generate_content", "待写入 Word 正文", step_id=ids[8], depends_on=(ids[1], ids[2], ids[3], ids[7])),
            _step(10, "生成 Word Diff 预览", StepType.GENERATE, "preview_word_diff", "冻结的 Diff 预览", step_id=ids[9], depends_on=(ids[8],)),
            _step(11, "等待用户确认", StepType.CONFIRM, "confirm_action", "用户确认或取消", step_id=ids[10], depends_on=(ids[9],), node_type=NodeType.APPROVAL),
            _step(12, "执行确认写入", StepType.WRITE, "write_word", "新 Word 版本", step_id=ids[11], depends_on=(ids[10],)),
        ),
    )


def _step(
    sequence: int,
    description: str,
    step_type: StepType,
    tool_name: str | None,
    expected_output: str,
    *,
    step_id: str | None = None,
    depends_on: tuple[str, ...] = (),
    node_type: NodeType = NodeType.TOOL,
) -> PlannedStep:
    return PlannedStep(
        sequence=sequence,
        step_name=description,
        step_type=step_type,
        tool_name=tool_name,
        input={"source": "user_message_or_previous_steps"},
        expected_output=expected_output,
        step_id=step_id or uuid4().hex,
        depends_on=depends_on,
        node_type=node_type,
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
