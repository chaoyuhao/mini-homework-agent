import ipaddress
import json
import math
from email.utils import parsedate_to_datetime
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import settings


class RemoteError(RuntimeError):
    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class RequestPacer:
    """One shared clock for a client's pages, downloads, and retries."""
    def __init__(self, seconds):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 <= seconds <= 300:
            raise ValueError("请求间隔必须为 0–300 秒")
        self.seconds = seconds
        self.next_start = 0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            delay = self.next_start - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_start = time.monotonic() + self.seconds


def chat_completions_url(base):
    base = base.rstrip("/")
    return base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


def origin(url):
    p = urllib.parse.urlsplit(url)
    return p.scheme, p.hostname, p.port or 443


def public_https(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise RemoteError("只允许 HTTPS 下载")
    try:
        addresses = socket.getaddrinfo(p.hostname, p.port or 443, type=socket.SOCK_STREAM)
        if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise RemoteError("拒绝下载内部网络地址")
    except socket.gaierror:
        raise RemoteError("下载域名解析失败") from None


def request(url, *, token="", method="GET", payload=None, timeout=45, max_bytes=12_000_000, retries=2, before_request=None, accept="application/json"):
    mode = settings().get("network_mode", "system")
    if mode not in ("system", "direct"):
        raise ValueError("network_mode 必须为 system 或 direct")
    opener = DIRECT_OPENER if mode == "direct" else OPENER
    headers = {"Accept": accept, "User-Agent": "elearning-grader/0.1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    attempts = retries + 1 if method in ("GET", "POST") else 1
    for attempt in range(attempts):
        if before_request:
            before_request()
        try:
            with opener.open(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=timeout) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise RemoteError("响应文件超过大小限制")
                return body, dict(response.headers)
        except urllib.error.HTTPError as e:
            code, response_headers = e.code, dict(e.headers)
            e.close()
            if code in (301, 302, 303, 307, 308):
                return None, {"redirect": response_headers.get("Location", "")}
            if code in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                retry_after = response_headers.get("Retry-After", "")
                try:
                    delay = float(retry_after)
                    if not math.isfinite(delay):
                        raise ValueError()
                except ValueError:
                    try:
                        delay = parsedate_to_datetime(retry_after).timestamp() - time.time()
                    except (TypeError, ValueError, OverflowError):
                        delay = 2 ** attempt
                delay = max(0, delay)
                time.sleep(delay)
                continue
            raise RemoteError(f"远程接口 HTTP {code}", status_code=code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
                continue
            raise RemoteError("远程接口连接失败或超时") from None


def json_request(url, **kwargs):
    body, headers = request(url, **kwargs)
    if body is None:
        raise RemoteError("API 返回登录/重定向，请检查凭证")
    try:
        return json.loads(body), headers
    except (ValueError, UnicodeError):
        raise RemoteError("API 未返回有效 JSON") from None
