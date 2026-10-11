import re
import urllib.parse

from .config import settings
from .net import RemoteError, json_request, origin, public_https, request


class Canvas:
    def __init__(self):
        cfg = settings()
        self.base = cfg["canvas_base_url"].rstrip("/")
        self.token = cfg["canvas_token"]
        if not self.token:
            raise ValueError("请设置 CANVAS_TOKEN 或 .secrets.json")
        if not self.base.startswith("https://"):
            raise ValueError("Canvas 地址必须使用 HTTPS")

    def api(self, path, *, params=None, method="GET", payload=None):
        url = self.base + "/api/v1/" + path.lstrip("/")
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        return json_request(url, token=self.token, method=method, payload=payload)[0]

    def pages(self, path, params=None):
        params = {"per_page": 100, **(params or {})}
        url = self.base + "/api/v1/" + path + "?" + urllib.parse.urlencode(params, doseq=True)
        seen = set()
        while url:
            if url in seen or origin(url) != origin(self.base) or not urllib.parse.urlsplit(url).path.startswith("/api/v1/"):
                raise RemoteError("分页地址无效")
            seen.add(url)
            rows, headers = json_request(url, token=self.token)
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

    def file(self, file_id):
        meta = self.api(f"files/{int(file_id)}")
        url = meta["url"]
        for _ in range(6):
            public_https(url)
            body, headers = request(url, token=self.token if origin(url) == origin(self.base) else "", max_bytes=10_000_000)
            if body is not None:
                return meta.get("display_name", meta.get("filename", "file")), body
            url = urllib.parse.urljoin(url, headers["redirect"])
        raise RemoteError("附件下载重定向过多")

    def publish(self, course, assignment, user, score, comment):
        # Deliberately no automatic retry: a timed-out PUT may already have added a comment.
        return self.api(f"courses/{int(course)}/assignments/{int(assignment)}/submissions/{int(user)}", method="PUT", payload={"submission": {"posted_grade": str(score)}, "comment": {"text_comment": comment}})
