"""SSRF-resistant, bounded Direct URL fetch for public Web pages."""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
import time
from typing import Protocol
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from backend.services.web_models import WebDocument
from backend.services.web_page_parser import WebPageParseError, parse_web_page


ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443}
ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 8.0
BLOCKED_METADATA_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata.aws.internal",
    "instance-data",
    "instance-data.ec2.internal",
})


class URLFetchError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class HostResolver(Protocol):
    def resolve(self, hostname: str, port: int) -> list[str]: ...


class URLTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        resolved_ips: list[str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> "TransportResponse": ...


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


class SocketHostResolver:
    def resolve(self, hostname: str, port: int) -> list[str]:
        try:
            records = socket.getaddrinfo(
                hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
            )
        except socket.gaierror as exc:
            raise URLFetchError("WEB_DNS_ERROR", "URL 域名无法解析") from exc
        return list(dict.fromkeys(str(record[4][0]) for record in records))


class PinnedHTTPTransport:
    """Try validated IPs within one deadline while preserving Host and TLS SNI."""

    def get(
        self,
        url: str,
        *,
        resolved_ips: list[str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> TransportResponse:
        parts = urlsplit(url)
        hostname = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if not resolved_ips:
            raise URLFetchError("WEB_DNS_ERROR", "URL 没有可用的公网地址")
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        for resolved_ip in resolved_ips:
            connection: socket.socket | ssl.SSLSocket | None = None
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                connection = socket.create_connection(
                    (resolved_ip, port), timeout=remaining
                )
                connection.settimeout(max(0.001, deadline - time.monotonic()))
                if parts.scheme == "https":
                    connection = ssl.create_default_context().wrap_socket(
                        connection, server_hostname=hostname
                    )
                    connection.settimeout(max(0.001, deadline - time.monotonic()))
                target = _request_target(parts.path, parts.query)
                host_header = f"[{hostname}]" if ":" in hostname else hostname
                if port not in {80, 443}:
                    host_header = f"{host_header}:{port}"
                request = (
                    f"GET {target} HTTP/1.1\r\n"
                    f"Host: {host_header}\r\n"
                    "User-Agent: StudentDocumentAgent/5.1\r\n"
                    "Accept: text/html,application/xhtml+xml,text/plain;q=0.8\r\n"
                    "Accept-Encoding: identity\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
                connection.sendall(request)
                response = http.client.HTTPResponse(connection)
                response.begin()
                headers = {
                    key.casefold(): value for key, value in response.getheaders()
                }
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise URLFetchError(
                        "WEB_RESPONSE_TOO_LARGE", "网页响应超过大小上限"
                    )
                return TransportResponse(int(response.status), headers, body)
            except URLFetchError:
                raise
            except (TimeoutError, socket.timeout) as exc:
                last_error = exc
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                last_error = exc
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
        if time.monotonic() >= deadline or isinstance(
            last_error, (TimeoutError, socket.timeout)
        ):
            raise URLFetchError("WEB_FETCH_TIMEOUT", "网页访问超时") from last_error
        raise URLFetchError("WEB_FETCH_FAILED", "网页无法访问") from last_error


class URLFetcher:
    def __init__(
        self,
        *,
        resolver: HostResolver | None = None,
        transport: URLTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self.resolver = resolver or SocketHostResolver()
        self.transport = transport or PinnedHTTPTransport()
        self.timeout_seconds = float(timeout_seconds)
        self.max_response_bytes = int(max_response_bytes)
        self.max_redirects = int(max_redirects)

    def fetch(self, url: str) -> WebDocument:
        current = canonicalize_url(url)
        redirects = 0
        while True:
            hostname, port, resolved_ips = validate_public_url(
                current, resolver=self.resolver
            )
            # The transport receives only the policy-approved addresses and must
            # connect to one of them, closing the DNS rebinding gap.
            response = self.transport.get(
                current,
                resolved_ips=resolved_ips,
                timeout_seconds=self.timeout_seconds,
                max_bytes=self.max_response_bytes,
            )
            if response.status_code in REDIRECT_STATUSES:
                location = response.headers.get("location", "").strip()
                if not location:
                    raise URLFetchError("WEB_REDIRECT_INVALID", "网页重定向缺少目标 URL")
                if redirects >= self.max_redirects:
                    raise URLFetchError("WEB_REDIRECT_LIMIT", "网页重定向次数超过上限")
                current = canonicalize_url(urljoin(current, location))
                redirects += 1
                continue
            if not 200 <= response.status_code < 300:
                raise URLFetchError(
                    "WEB_HTTP_ERROR", f"网页返回 HTTP {response.status_code}"
                )
            if len(response.body) > self.max_response_bytes:
                raise URLFetchError("WEB_RESPONSE_TOO_LARGE", "网页响应超过大小上限")
            if response.headers.get("content-encoding", "identity").casefold() not in {
                "", "identity"
            }:
                raise URLFetchError("WEB_CONTENT_ENCODING_UNSUPPORTED", "网页压缩格式不受支持")
            content_type, encoding = _content_type(response.headers.get("content-type", ""))
            if content_type not in ALLOWED_CONTENT_TYPES:
                raise URLFetchError("WEB_CONTENT_TYPE_UNSUPPORTED", "URL 不是受支持的网页文本格式")
            try:
                return parse_web_page(
                    response.body,
                    fetched_url=current,
                    content_type=content_type,
                    encoding=encoding,
                    response_metadata={
                        "http_status": response.status_code,
                        "redirect_count": redirects,
                        "resolved_host": hostname,
                        "resolved_port": port,
                    },
                )
            except WebPageParseError as exc:
                raise URLFetchError("WEB_PARSE_ERROR", str(exc)) from exc


def canonicalize_url(url: str) -> str:
    try:
        parts = urlsplit(str(url or "").strip())
        port = parts.port
    except ValueError as exc:
        raise URLFetchError("INVALID_WEB_URL", "URL 格式无效") from exc
    scheme = parts.scheme.casefold()
    if scheme not in ALLOWED_SCHEMES or not parts.hostname:
        raise URLFetchError("INVALID_WEB_URL", "仅支持带域名的 HTTP(S) URL")
    if parts.username is not None or parts.password is not None:
        raise URLFetchError("INVALID_WEB_URL", "URL 不允许包含用户名或密码")
    hostname = parts.hostname.casefold().rstrip(".")
    if not hostname or any(character in hostname for character in "\r\n\t%"):
        raise URLFetchError("INVALID_WEB_URL", "URL 域名无效")
    try:
        ipaddress.ip_address(hostname)
        ascii_host = hostname
    except ValueError:
        try:
            ascii_host = hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise URLFetchError("INVALID_WEB_URL", "URL 域名无效") from exc
    resolved_port = port or (443 if scheme == "https" else 80)
    if resolved_port not in ALLOWED_PORTS:
        raise URLFetchError("WEB_PORT_BLOCKED", "URL 端口不在安全允许范围")
    display_host = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    netloc = display_host
    if port is not None and port != (443 if scheme == "https" else 80):
        netloc = f"{display_host}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def validate_public_url(
    url: str, *, resolver: HostResolver
) -> tuple[str, int, list[str]]:
    canonical = canonicalize_url(url)
    parts = urlsplit(canonical)
    hostname = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if hostname in BLOCKED_METADATA_HOSTS:
        raise URLFetchError("WEB_SSRF_BLOCKED", "URL 指向云环境 metadata 服务")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(
        (".localhost", ".local", ".internal")
    ):
        raise URLFetchError("WEB_SSRF_BLOCKED", "URL 指向非公网主机")
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = [str(literal)]
    except ValueError:
        addresses = resolver.resolve(hostname, port)
    if not addresses:
        raise URLFetchError("WEB_DNS_ERROR", "URL 没有可用地址")
    checked = []
    for raw_address in addresses:
        try:
            address = ipaddress.ip_address(raw_address.split("%", 1)[0])
        except ValueError as exc:
            raise URLFetchError("WEB_DNS_ERROR", "域名解析结果无效") from exc
        if not address.is_global:
            raise URLFetchError("WEB_SSRF_BLOCKED", "URL 指向非公网地址")
        checked.append(str(address))
    return hostname, port, list(dict.fromkeys(checked))


def _request_target(path: str, query: str) -> str:
    safe_path = quote(path or "/", safe="/%:@!$&'()*+,;=-._~")
    safe_query = quote(query, safe="=&;%:+,/?@!$'()*-._~")
    return safe_path + (f"?{safe_query}" if safe_query else "")


def _content_type(value: str) -> tuple[str, str | None]:
    parts = [item.strip() for item in str(value or "").split(";")]
    media_type = parts[0].casefold() if parts and parts[0] else ""
    encoding = None
    for parameter in parts[1:]:
        if parameter.casefold().startswith("charset="):
            encoding = parameter.split("=", 1)[1].strip().strip("\"'")
    return media_type, encoding


__all__ = [
    "BLOCKED_METADATA_HOSTS",
    "HostResolver",
    "PinnedHTTPTransport",
    "SocketHostResolver",
    "TransportResponse",
    "URLFetchError",
    "URLFetcher",
    "URLTransport",
    "canonicalize_url",
    "validate_public_url",
]
