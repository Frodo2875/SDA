"""Deterministic lightweight plans for only the complex task classes in scope."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlannedStep:
    sequence: int
    step_name: str
    step_type: str
    tool_name: str | None = None


@dataclass(frozen=True)
class TaskPlan:
    task_type: str
    steps: tuple[PlannedStep, ...]


def create_plan(user_message: str) -> TaskPlan | None:
    """Return None for simple requests so V1 queries keep their lightweight path."""
    if _is_scholarship_task(user_message):
        return TaskPlan(
            task_type="scholarship_evaluation",
            steps=(
                PlannedStep(1, "确认学生身份", "tool", "search_student"),
                PlannedStep(2, "查询学生成绩", "tool", "query_table"),
                PlannedStep(3, "查询科研成果", "tool", "query_table"),
                PlannedStep(4, "检索奖学金评审办法", "tool", "retrieve_document"),
                PlannedStep(5, "执行奖学金规则判断", "tool", "evaluate_scholarship_eligibility"),
                PlannedStep(6, "生成有证据的解释", "generation"),
            ),
        )
    if _is_write_task(user_message):
        return TaskPlan(
            task_type="confirmed_word_write",
            steps=(
                PlannedStep(1, "确认学生身份", "tool", "get_student_info"),
                PlannedStep(2, "查询学生成绩", "tool", "get_student_scores"),
                PlannedStep(3, "查询科研成果", "tool", "get_student_research"),
                PlannedStep(4, "生成待写入内容", "generation"),
                PlannedStep(5, "等待用户确认", "confirmation"),
                PlannedStep(6, "执行确认写入", "side_effect", "write_word"),
            ),
        )
    return None


def _is_scholarship_task(message: str) -> bool:
    return "奖学金" in message and any(
        word in message for word in ("判断", "满足", "符合", "条件", "资格")
    )


def _is_write_task(message: str) -> bool:
    return ".docx" in message.casefold() and any(
        word in message for word in ("写入", "写进")
    )
