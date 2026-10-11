import json
import re
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import workflow
from .canvas import Canvas
from .config import DATA, ROOT, models_from_doc, save_json, selected_model, settings
from .net import RemoteError


def serve(port=8765):
    csrf = secrets.token_urlsafe(32)
    jobs = {}
    job_lock = threading.Lock()
    address = f"127.0.0.1:{port}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, data, status=200, content_type="application/json; charset=utf-8"):
            raw = json.dumps(data, ensure_ascii=False).encode() if content_type.startswith("application/json") else data.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", f"default-src 'self'; script-src 'nonce-{csrf}'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(raw)

        def valid_host(self):
            return self.headers.get("Host") == address

        def do_GET(self):
            if not self.valid_host():
                return self.reply({"error": "请使用 127.0.0.1 地址"}, 403)
            p = urllib.parse.urlsplit(self.path)
            q = dict(urllib.parse.parse_qsl(p.query))
            try:
                if p.path == "/":
                    from pathlib import Path
                    return self.reply(Path(__file__).with_name("index.html").read_text().replace("__CSRF__", csrf), content_type="text/html; charset=utf-8")
                if p.path == "/api/state":
                    report = DATA / "benchmark.json"
                    try:
                        models = [m["name"] for m in models_from_doc()]
                        model = selected_model()["name"]
                    except ValueError:
                        models, model = [], "未配置模型"
                    return self.reply({"model": model, "models": models, "benchmark": json.loads(report.read_text()) if report.exists() else None, "runs": workflow.runs(), "jobs": list(jobs.values())})
                if p.path == "/api/courses":
                    courses = Canvas().courses()
                    rows = [{"id": c["id"], "name": c.get("name", str(c["id"]))} for c in courses if any(e.get("type") in ("teacher", "ta") for e in c.get("enrollments", []))]
                    return self.reply(rows)
                if p.path == "/api/assignments":
                    rows = Canvas().assignments(int(q["course_id"]))
                    return self.reply([{k: a.get(k) for k in ("id", "name", "points_possible", "due_at", "rubric", "published", "submission_types", "group_category_id")} for a in rows])
                if p.path == "/api/run":
                    return self.reply(workflow.load_run(q["id"]))
                if p.path == "/api/attachment":
                    run = workflow.load_run(q["run_id"])
                    row = next(r for r in run["results"] if r["user_id"] == int(q["user_id"]))
                    attachment = next(a for a in row.get("attachments", []) if a["id"] == int(q["file_id"]))
                    if not re.fullmatch(r"\d+\.[a-z0-9]{1,12}", attachment["stored_name"]):
                        raise ValueError("附件路径无效")
                    path = DATA / "attachments" / run["id"] / str(row["user_id"]) / attachment["stored_name"]
                    raw = path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", 'attachment; filename="' + attachment["stored_name"] + '"')
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    return self.wfile.write(raw)
                return self.reply({"error": "页面不存在"}, 404)
            except Exception as e:
                return self.error(e)

        def error(self, e):
            message = str(e) if isinstance(e, (ValueError, RemoteError)) else "操作失败：" + type(e).__name__
            if "HTTP 401" in message:
                message = "Canvas 返回 401（访问令牌未被接受）。请检查凭证；本次没有写入成绩。"
            self.reply({"error": message}, 400)

        def do_POST(self):
            if not self.valid_host() or self.headers.get("X-CSRF-Token") != csrf or self.headers.get("Origin") not in (None, "http://" + address):
                return self.reply({"error": "请求校验失败，请刷新页面"}, 403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1_000_000:
                    raise ValueError("请求大小无效")
                payload = json.loads(self.rfile.read(length))
                if self.path == "/api/token":
                    token = payload.get("token", "").strip()
                    if not token or len(token) > 4096 or any(c.isspace() for c in token):
                        raise ValueError("令牌格式无效")
                    cfg = settings()
                    cfg["canvas_token"] = token
                    save_json(ROOT / ".secrets.json", cfg)
                    return self.reply({"saved": True})
                if self.path == "/api/approve":
                    row = workflow.approve(payload["run_id"], payload["user_id"], payload["score"], payload["feedback"], payload.get("reviewer_note", ""))
                    return self.reply(row)
                if self.path in ("/api/grade", "/api/demo", "/api/publish", "/api/reconcile"):
                    with job_lock:
                        if any(j["status"] == "running" for j in jobs.values()):
                            raise ValueError("当前任务仍在运行，请稍候")
                        job_id = secrets.token_hex(8)
                        job = {"id": job_id, "status": "running", "kind": self.path, "completed": 0, "total": 0}
                        jobs[job_id] = job
                    action = self.path
                    def work():
                        try:
                            if action == "/api/demo":
                                run = workflow.demo(offline=bool(payload.get("offline")))
                            elif action == "/api/publish":
                                if payload.get("confirm") != "publish_approved":
                                    raise ValueError("回写需要明确确认")
                                run = workflow.publish(payload["run_id"])
                            elif action == "/api/reconcile":
                                run = workflow.reconcile(payload["run_id"])
                            else:
                                limit = int(payload.get("limit", 5))
                                if not 0 <= limit <= 10000:
                                    raise ValueError("批改数量无效")
                                def progress(r):
                                    job.update(run_id=r["id"], completed=len(r["results"]), total=r["total"])
                                run = workflow.batch(int(payload["course_id"]), int(payload["assignment_id"]), requirements=payload.get("requirements", ""), limit=limit, model_name=payload.get("model"), custom_rubric=payload.get("rubric"), progress=progress)
                            job.update(status="complete", run_id=run["id"], completed=len(run["results"]), total=run["total"])
                        except Exception as e:
                            job.update(status="error", error=str(e) if isinstance(e, (ValueError, RuntimeError)) else type(e).__name__)
                    threading.Thread(target=work, daemon=True).start()
                    return self.reply(job, 202)
                return self.reply({"error": "接口不存在"}, 404)
            except Exception as e:
                self.error(e)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"审核页面：http://{address}（Ctrl+C 停止）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
