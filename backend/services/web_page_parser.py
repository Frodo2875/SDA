"""Deterministic HTML-to-WebDocument parser with no model inference."""

from hashlib import sha256
from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from backend.services.web_models import WebDocument
from backend.services.web_safety import assess_web_content


MAX_HTML_CHARACTERS = 2_000_000
MAX_CONTENT_CHARACTERS = 1_000_000
_SPACE = re.compile(r"\s+")
_SKIPPED_TAGS = {"script", "style", "noscript", "template", "svg", "canvas"}


class WebPageParseError(ValueError):
    """The response cannot be represented as a bounded WebDocument."""


class _PageHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.content_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.canonical_href: str | None = None
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        clean_tag = tag.casefold()
        attributes = {str(key).casefold(): str(value or "") for key, value in attrs}
        if clean_tag in _SKIPPED_TAGS:
            self._skip_depth += 1
        if clean_tag == "title":
            self._in_title = True
        if clean_tag == "meta":
            key = (
                attributes.get("property")
                or attributes.get("name")
                or attributes.get("itemprop")
            ).strip().casefold()
            content = attributes.get("content", "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content
        if clean_tag == "link":
            relations = {item.casefold() for item in attributes.get("rel", "").split()}
            href = attributes.get("href", "").strip()
            if "canonical" in relations and href and self.canonical_href is None:
                self.canonical_href = href

    def handle_endtag(self, tag: str) -> None:
        clean_tag = tag.casefold()
        if clean_tag == "title":
            self._in_title = False
        if clean_tag in _SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        clean = _SPACE.sub(" ", data).strip()
        if not clean or self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(clean)
        else:
            self.content_parts.append(clean)


def parse_web_page(
    html: bytes | str,
    *,
    fetched_url: str,
    content_type: str = "text/html",
    encoding: str | None = None,
    response_metadata: dict[str, Any] | None = None,
) -> WebDocument:
    """Extract explicit page fields; absent metadata remains None."""
    text, used_encoding = _decode_html(html, encoding)
    if len(text) > MAX_HTML_CHARACTERS:
        raise WebPageParseError("网页文本超过解析上限")
    parser = _PageHTMLParser()
    try:
        parser.feed(text)
        parser.close()
    except (ValueError, AssertionError) as exc:
        raise WebPageParseError("HTML 格式异常") from exc
    title = _bounded(_first_nonempty(
        " ".join(parser.title_parts),
        parser.meta.get("og:title"),
        parser.meta.get("twitter:title"),
    ), 500)
    canonical_url = _metadata_url(parser.canonical_href, fetched_url) or fetched_url
    domain = (urlsplit(canonical_url).hostname or urlsplit(fetched_url).hostname or "").casefold()
    if not domain:
        raise WebPageParseError("网页 URL 缺少有效域名")
    publisher = _bounded(_first_nonempty(
        parser.meta.get("og:site_name"),
        parser.meta.get("publisher"),
        parser.meta.get("article:publisher"),
    ), 500)
    published_at = _bounded(_first_nonempty(
        parser.meta.get("article:published_time"),
        parser.meta.get("datepublished"),
        parser.meta.get("date"),
    ), 128)
    content = "\n".join(parser.content_parts)
    content = content[:MAX_CONTENT_CHARACTERS]
    identity = sha256(
        f"{fetched_url}\n{canonical_url}\n{sha256(text.encode('utf-8')).hexdigest()}".encode("utf-8")
    ).hexdigest()[:32]
    metadata = {
        "content_type": content_type,
        "encoding": used_encoding,
        "description": _bounded(_first_nonempty(
            parser.meta.get("description"), parser.meta.get("og:description")
        ), 4000),
        **dict(response_metadata or {}),
    }
    security = assess_web_content(title, content, publisher, metadata)
    return WebDocument(
        document_id=identity,
        url=fetched_url,
        title=title,
        content=content,
        canonical_url=canonical_url,
        domain=domain,
        publisher=publisher,
        published_at=published_at,
        metadata={key: value for key, value in metadata.items() if value is not None},
        untrusted_content=True,
        detected_untrusted_patterns=security["detected_untrusted_patterns"],
    )


def _decode_html(value: bytes | str, encoding: str | None) -> tuple[str, str]:
    if isinstance(value, str):
        return value, encoding or "unicode"
    candidates = [encoding] if encoding else []
    candidates.extend(["utf-8-sig", "gb18030", "big5"])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return value.decode(candidate), candidate
        except (LookupError, UnicodeDecodeError):
            continue
    raise WebPageParseError("网页字符编码无法可靠识别")


def _metadata_url(candidate: str | None, base_url: str) -> str | None:
    if not candidate:
        return None
    absolute = urljoin(base_url, candidate.strip())
    if len(absolute) > 4096:
        return None
    parts = urlsplit(absolute)
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    return urlunsplit((parts.scheme.casefold(), parts.netloc, parts.path or "/", parts.query, ""))


def _first_nonempty(*values: str | None) -> str | None:
    return next((value.strip() for value in values if value and value.strip()), None)


def _bounded(value: str | None, limit: int) -> str | None:
    return value[:limit] if value is not None else None


__all__ = ["WebPageParseError", "parse_web_page"]
