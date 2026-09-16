"""Plain-language labels; backend values and decisions remain unchanged."""

import re

LABELS = {
    "CREATED": "待开始", "RUNNING": "进行中", "WAITING_TOOL": "处理中",
    "COMPLETED": "已完成", "FAILED": "失败", "CANCELLED": "已取消",
    "PAUSED": "已暂停", "WAITING_CONFIRMATION": "待确认", "PARTIAL_SUCCESS": "部分完成",
    "PLANNING": "准备中", "LOCAL_RETRIEVAL": "查找文档", "WEB_RETRIEVAL": "查找网页",
    "EVIDENCE_CHECK": "核对资料", "GENERATING": "整理结果",
    "DRAFT": "草稿", "EXPORTED": "已导出", "PENDING_APPROVAL": "待确认",
    "READY": "可用", "EMPTY": "暂无文件", "INDEXING": "整理中",
    "INDEXED": "可查询", "UPLOADED": "已上传", "PROCESSING": "处理中",
    "ONLINE": "在线", "UNAVAILABLE": "暂不可用", "AVAILABLE": "可用",
    "SUCCESS": "已完成", "PENDING": "待处理", "QUEUED": "排队中",
    "NOT_REQUIRED": "无需处理", "PARSED": "已读取", "DETECTING": "识别中",
    "PARSING": "读取中", "REPROCESSING": "重新整理中", "REINDEXING": "更新中",
    "OCR_PROCESSING": "识别文字中", "VISUAL_PROCESSING": "识别图片中", "LAYOUT_PROCESSING": "分析排版中",
}


def label(value: object) -> str:
    text = str(value or "未提供")
    return LABELS.get(text.upper(), text)


def stage_text(value: str) -> str:
    return re.sub(r"\b[A-Z][A-Z_]+\b", lambda match: label(match.group()), value)
