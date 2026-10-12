import re
import urllib.parse

from .config import settings
from .net import RemoteError, RequestPacer, json_request, origin, public_https, request


class CanvasAccessError(RemoteError):
    """Credential or permission rejection on the Canvas origin itself."""


class Canvas:
    def __init__(self, *, request_interval=0):
        self.pacer = RequestPacer(request_interval)
        self.access_error = None
        cfg = settings()
        self.base = cfg["canvas_base_url"].rstrip("/")
        self.token = cfg["canvas_token"]
        if not self.token:
            raise ValueError("请设置 CANVAS_TOKEN 或 .secrets.json")
        if not self.base.startswith("https://"):
            raise ValueError("Canvas 地址必须使用 HTTPS")

    def _before_request(self):
        if self.access_error:
            raise self.access_error
        self.pacer.wait()
        if self.access_error:
            raise self.access_error

    def _request(self, url, *, as_json=False, **kwargs):
        try:
            return (json_request if as_json else request)(url, before_request=self._before_request, **kwargs)
        except RemoteError as e:
            if origin(url) == origin(self.base) and e.status_code in (401, 403):
                detail = "凭证未被接受，请检查或更新令牌" if e.status_code == 401 else "当前账号没有访问权限，请检查课程助教权限"
                self.access_error = CanvasAccessError(f"Canvas HTTP {e.status_code}：{detail}", status_code=e.status_code)
                raise self.access_error from None
            raise

    def api(self, path, *, params=None, method="GET", payload=None):
        url = self.base + "/api/v1/" + path.lstrip("/")
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        return self._request(url, as_json=True, token=self.token, method=method, payload=payload)[0]

    def pages(self, path, params=None):
        params = {"per_page": 100, **(params or {})}
        url = self.base + "/api/v1/" + path + "?" + urllib.parse.urlencode(params, doseq=True)
        seen = set()
        while url:
            if url in seen or origin(url) != origin(self.base) or not urllib.parse.urlsplit(url).path.startswith("/api/v1/"):
                raise RemoteError("分页地址无效")
            seen.add(url)
            rows, headers = self._request(url, as_json=True, token=self.token)
            if not isinstance(rows, list):
                raise RemoteError("分页接口未返回列表")
            yield from rows
            links = headers.get("Link", headers.get("link", ""))
            nxt = re.search(r'<([^>]+)>;\s*rel="next"', links)
            url = nxt.group(1) if nxt else None

    def courses(self):
        return list(self.pages("courses"))

    def assignments(self, course):
        return list(self.pages(f"courses/{int(course)}/assignments"))

    def assignment(self, course, assignment):
        return self.api(f"courses/{int(course)}/assignments/{int(assignment)}")

    def submissions(self, course, assignment):
        return list(self.pages(f"courses/{int(course)}/assignments/{int(assignment)}/submissions", {"include[]": ["user"]}))

    def submission(self, course, assignment, user):
        return self.api(f"courses/{int(course)}/assignments/{int(assignment)}/submissions/{int(user)}", params={"include[]": ["submission_comments"]})

    def file(self, file_id, *, submission_id=None):
        meta = self.api(f"files/{int(file_id)}")
        url = meta["url"]
        seen = set()
        used_preview = False
        for _ in range(6):
            if url in seen:
                if used_preview:
                    raise RemoteError("附件下载出现循环重定向，请重试或人工查看")
                # Some deployments loop on /download. This read-only endpoint checks
                # the caller's permission and returns a direct, signed storage URL.
                params = {"submission_id": int(submission_id)} if submission_id is not None else None
                preview = self.api(f"files/{int(file_id)}/public_url", params=params)
                url = preview.get("public_url")
                if not isinstance(url, str) or not url or url in seen:
                    raise RemoteError("附件下载出现循环重定向，平台未提供可用直链")
                used_preview = True
            seen.add(url)
            public_https(url)
            # Download binary content rather than requesting a JSON representation.
            body, headers = self._request(url, token=self.token if origin(url) == origin(self.base) else "", max_bytes=10_000_000, accept="*/*")
            if body is not None:
                size = meta.get("size")
                if isinstance(size, int) and not isinstance(size, bool) and len(body) != size:
                    raise RemoteError("附件下载大小与平台记录不一致，请重试或人工查看")
                return meta.get("display_name", meta.get("filename", "file")), body
            url = urllib.parse.urljoin(url, headers["redirect"])
        raise RemoteError("附件下载重定向过多")

    def publish(self, course, assignment, user, score, comment):
        # Deliberately no automatic retry: a timed-out PUT may already have added a comment.
        return self.api(f"courses/{int(course)}/assignments/{int(assignment)}/submissions/{int(user)}", method="PUT", payload={"submission": {"posted_grade": str(score)}, "comment": {"text_comment": comment}})
