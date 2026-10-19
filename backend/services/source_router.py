"""Deterministic source strategy hints for local and bounded Web retrieval."""

from enum import Enum
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceStrategy(str, Enum):
    LOCAL = "LOCAL"
    WEB = "WEB"
    BOTH = "BOTH"
    DIRECT_URL = "DIRECT_URL"


class SourceRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_strategy: SourceStrategy
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    urls: list[str] = Field(default_factory=list, max_length=5)
    local_retrieval_compatible: Literal[True] = True
    agentic_retrieval_compatible: Literal[True] = True


URL_PATTERN = re.compile(r"https?://[^\s<>'\"，。；]+", re.I)
LOCAL_SIGNALS = (
    "上传", "本地", "知识库", "材料", "这份文档", "该文档", "论文", "附件",
)
WEB_SIGNALS = (
    "联网", "网页", "互联网", "网上", "网络", "在线", "官网", "公开信息",
    "最新", "今日", "当前政策", "新闻", "实时", "天气",
)
BOTH_SIGNALS = (
    "结合本地", "结合材料", "对照材料", "本地和网页", "本地与网页", "材料和最新",
    "结合网上", "结合网络", "结合在线",
)

# A source restriction takes precedence over positive keywords and URLs.  Keep
# "不联网" intact: deleting just the negation would accidentally grant Web tools.
NETWORK_DENIAL = re.compile(
    r"(?:不(?:要|用|需(?:要)?|必)?|禁止|严禁|无需|勿)\s*"
    r"(?:联网|上网|(?:访问|抓取|读取|搜索|查询|查阅|查)\s*(?:网页|网站|互联网|网络|网上))"
    r"|\b(?:do\s+not|don't|never|no)\s+(?:browse|browsing|web\s+search|internet\s+access)\b"
    r"|\b(?:offline|local\s+(?:files?\s+)?only)\b",
    re.I,
)
LOCAL_ONLY = re.compile(
    r"(?:^|[，。；;！!\n\s])(?:请你|请)?(?:仅|只)(?:能|允许)?\s*"
    r"(?:使用|用|依据|根据|查询|分析|读取|检索|处理|参考)?\s*(?:本地|上传|知识库|附件)"
)


def route_source(task: str) -> dict[str, object]:
    """Return a safe source hint without executing retrieval or changing scope."""
    clean = str(task or "").strip()
    urls = URL_PATTERN.findall(clean)
    if NETWORK_DENIAL.search(clean) or LOCAL_ONLY.search(clean):
        route = SourceRoute(
            source_strategy=SourceStrategy.LOCAL,
            reason_code="EXPLICIT_LOCAL_ONLY_RESTRICTION",
        )
    elif urls:
        route = SourceRoute(
            source_strategy=SourceStrategy.DIRECT_URL,
            reason_code="EXPLICIT_DIRECT_URL",
            urls=urls[:5],
        )
    elif any(signal in clean for signal in BOTH_SIGNALS) or (
        any(signal in clean for signal in LOCAL_SIGNALS)
        and any(signal in clean for signal in WEB_SIGNALS)
    ):
        route = SourceRoute(
            source_strategy=SourceStrategy.BOTH,
            reason_code="EXPLICIT_LOCAL_AND_WEB_SIGNALS",
        )
    elif any(signal in clean for signal in WEB_SIGNALS):
        route = SourceRoute(
            source_strategy=SourceStrategy.WEB,
            reason_code="EXPLICIT_WEB_OR_FRESH_INFORMATION",
        )
    else:
        route = SourceRoute(
            source_strategy=SourceStrategy.LOCAL,
            reason_code=(
                "EXPLICIT_LOCAL_DOCUMENT_SIGNAL"
                if any(signal in clean for signal in LOCAL_SIGNALS)
                else "SAFE_LOCAL_DEFAULT"
            ),
        )
    return route.model_dump(mode="json")


__all__ = ["SourceRoute", "SourceStrategy", "route_source"]
