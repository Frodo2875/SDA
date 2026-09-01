"""Small deterministic redaction layer for traces and operational logs."""

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


PHONE_PATTERN = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![A-Za-z0-9.-])"
)
ID_PATTERN = re.compile(r"(?<!\d)(\d{6})(\d{8})(\d{3}[0-9Xx])(?!\d)")
ADDRESS_PATTERN = re.compile(
    r"([\u4e00-\u9fff]{2,}(?:省|市|自治区|自治州|县|区))"
    r"([\u4e00-\u9fffA-Za-z0-9号弄栋单元室路街道镇乡村]{4,})"
)
SENSITIVE_KEYS = {
    "phone", "mobile", "telephone", "email", "id_card", "identity_card",
    "address", "手机号", "联系电话", "移动电话", "邮箱", "身份证号", "身份证", "住址", "地址",
}
SENSITIVE_URL_QUERY_KEYS = frozenset({
    "access_token", "api_key", "apikey", "auth", "authorization", "code",
    "credential", "key", "password", "secret", "session", "session_id",
    "signature", "sig", "token",
})


def redact_text(value: str) -> str:
    """Redact common sensitive values while retaining diagnostic usefulness."""
    text = str(value)
    text = PHONE_PATTERN.sub(lambda match: f"{match.group(1)}****{match.group(3)}", text)
    text = EMAIL_PATTERN.sub(_redact_email, text)
    text = ID_PATTERN.sub(lambda match: f"{match.group(1)}********{match.group(3)}", text)
    text = ADDRESS_PATTERN.sub(lambda match: f"{match.group(1)}***", text)
    return text


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact JSON-compatible structures without mutating input."""
    normalized_key = str(key or "").strip().casefold()
    if normalized_key in {"url", "canonical_url"} and isinstance(value, str):
        return redact_url(value)
    if normalized_key in {item.casefold() for item in SENSITIVE_KEYS}:
        return _mask_sensitive_value(value, normalized_key)
    if isinstance(value, dict):
        return {item_key: redact_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_url(value: str) -> str:
    """Mask credentials in URL query values while retaining trace provenance."""
    text = str(value or "")
    try:
        parts = urlsplit(text)
        query = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        return redact_text(text)
    safe_query = urlencode([
        (key, "***" if key.casefold() in SENSITIVE_URL_QUERY_KEYS else redact_text(item))
        for key, item in query
    ], doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, safe_query, ""))


def redacted_json(value: Any, *, max_length: int = 1000) -> str:
    """Serialize a bounded redacted summary for Trace storage."""
    try:
        serialized = json.dumps(redact_value(value), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        serialized = redact_text(str(value))
    return serialized[: max(32, int(max_length))]


def _redact_email(match: re.Match[str]) -> str:
    local, domain = match.groups()
    visible = local[:1]
    return f"{visible}***@{domain}"


def _mask_sensitive_value(value: Any, key: str) -> Any:
    if value is None:
        return None
    text = str(value)
    if "phone" in key or "mobile" in key or "电话" in key or "手机" in key:
        return redact_text(text) if PHONE_PATTERN.search(text) else "***"
    if "email" in key or "邮箱" in key:
        return redact_text(text) if EMAIL_PATTERN.search(text) else "***"
    if "id" in key or "身份证" in key:
        return redact_text(text) if ID_PATTERN.search(text) else "***"
    return "***"
