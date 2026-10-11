import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request


class RemoteError(RuntimeError):
    pass


def chat_completions_url(base):
    base = base.rstrip("/")
    return base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


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


def request(url, *, token="", method="GET", payload=None, timeout=45, max_bytes=12_000_000, retries=2):
    headers = {"Accept": "application/json", "User-Agent": "elearning-grader/0.1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    attempts = retries + 1 if method in ("GET", "POST") else 1
    for attempt in range(attempts):
        try:
            with OPENER.open(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=timeout) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise RemoteError("响应文件超过大小限制")
                return body, dict(response.headers)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                return None, {"redirect": e.headers.get("Location", "")}
            if e.code in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                delay = min(float(e.headers.get("Retry-After", 2 ** attempt)) if e.headers.get("Retry-After", "").isdigit() else 2 ** attempt, 20)
                time.sleep(delay)
                continue
            raise RemoteError(f"远程接口 HTTP {e.code}") from None
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
