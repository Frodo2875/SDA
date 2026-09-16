"""Deterministic answers about supported document actions, not data claims."""

import re

_QUESTION = re.compile(
    r"(?:请问)?(?:你|你们|系统|助手)?(?:现在|目前)?"
    r"(?:是否(?:具有|具备|拥有|有|支持|可以|能够|能)|有(?:没有)?|具备|具有|拥有|支持|能否|能不能|可不可以|可以|能够|能)"
    r"(?:对)?(?:word(?:文档|文件)?(?:的)?(?:编辑|写入|修改)(?:权限|能力|功能)?|"
    r"(?:编辑|写入|修改)word(?:文档|文件)?(?:的权限|权限|能力|功能)?)"
    r"(?:吗|么|呢)?[?？。!！]*", re.I,
)

DOCUMENT_CAPABILITY_ANSWER = (
    "可以，但需要你确认后才会执行。\n\n"
    "三种聊天模式都可以在「文档操作」中编辑待保存正文，保存为新 Word / Markdown，"
    "或追加到已有可写 Word。先查看修改预览，再点击「确认」；取消不会修改文件。\n\n"
    "目前不支持直接替换 Word 原文或任意调整排版，只读文件也不能修改。"
)


def document_capability_answer(message: str) -> str | None:
    """Match only standalone capability questions, never an actual write request."""
    normalized = re.sub(r"\s+", "", message)
    return DOCUMENT_CAPABILITY_ANSWER if _QUESTION.fullmatch(normalized) else None
