"""Synthetic traffic only: pacing, credential stops, and safe batch recovery."""
import copy
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from grader import canvas, materials, net, workflow


class Response(io.BytesIO):
    def __init__(self, body=b"[]", headers=None):
        super().__init__(body)
        self.headers = headers or {}


class TrafficTests(unittest.TestCase):
    def setUp(self):
        route = patch.object(net, "settings", create=True, return_value={"network_mode": "system"})
        route.start()
        self.addCleanup(route.stop)

    def test_project_direct_mode_bypasses_system_proxy(self):
        with patch.object(net, "settings", return_value={"network_mode": "direct"}), patch.object(net, "DIRECT_OPENER", create=True) as direct, patch.object(net.OPENER, "open", side_effect=AssertionError("system proxy must not be used")):
            direct.open.return_value = Response(b"direct connection")
            self.assertEqual(net.request("https://school.example/api/v1/courses")[0], b"direct connection")

    def test_direct_connection_failure_does_not_silently_switch_to_proxy(self):
        with patch.object(net, "settings", return_value={"network_mode": "direct"}), patch.object(net, "DIRECT_OPENER", create=True) as direct, patch.object(net.OPENER, "open", side_effect=AssertionError("route must not silently change")):
            direct.open.side_effect = urllib.error.URLError(TimeoutError())
            with self.assertRaises(net.RemoteError):
                net.request("https://school.example/api/v1/courses", retries=0)

    def test_invalid_network_mode_fails_before_sending_credentials(self):
        with patch.object(net, "settings", return_value={"network_mode": "typo"}), patch.object(net.OPENER, "open", side_effect=AssertionError("no network before validation")):
            with self.assertRaises(ValueError):
                net.request("https://school.example/api/v1/courses", token="synthetic")

    def test_401_retains_status_and_is_not_retried(self):
        failure = urllib.error.HTTPError("https://school.example/api/v1/courses", 401, "unauthorized", {}, None)
        with patch.object(net.OPENER, "open", side_effect=failure) as connection:
            with self.assertRaises(net.RemoteError) as caught:
                net.request("https://school.example/api/v1/courses")
        self.assertEqual(getattr(caught.exception, "status_code", None), 401)
        self.assertEqual(connection.call_count, 1)

    def test_429_waits_for_full_retry_after(self):
        failure = urllib.error.HTTPError("https://school.example/api/v1/courses", 429, "limited", {"Retry-After": "30"}, None)
        with patch.object(net.OPENER, "open", side_effect=[failure, Response()]), patch.object(net.time, "sleep") as sleep:
            self.assertEqual(net.request("https://school.example/api/v1/courses")[0], b"[]")
        self.assertEqual(sleep.call_args.args[0], 30)

    def test_429_accepts_retry_after_http_date(self):
        failure = urllib.error.HTTPError("https://school.example/api/v1/courses", 429, "limited", {"Retry-After": "Thu, 01 Jan 1970 00:02:10 GMT"}, None)
        with patch.object(net.OPENER, "open", side_effect=[failure, Response()]), patch.object(net.time, "time", return_value=100), patch.object(net.time, "sleep") as sleep:
            net.request("https://school.example/api/v1/courses")
        self.assertEqual(sleep.call_args.args[0], 30)

    def test_canvas_spaces_pages_metadata_download_and_redirect(self):
        clock = [0.0]
        starts = []
        responses = iter([
            Response(headers={"Link": '<https://school.example/api/v1/courses?page=2>; rel="next"'}),
            Response(),
            Response(b'{"url":"https://school.example/files/42", "filename":"answer.py"}'),
            urllib.error.HTTPError("https://school.example/files/42", 302, "redirect", {"Location": "https://cdn.example/answer.py"}, None),
            Response(b"x=1"),
        ])
        def open_request(req, **kwargs):
            starts.append(clock[0])
            reply = next(responses)
            if isinstance(reply, Exception):
                raise reply
            return reply
        with patch.object(canvas, "settings", return_value={"canvas_base_url": "https://school.example", "canvas_token": "synthetic"}), patch.object(net.time, "monotonic", side_effect=lambda: clock[0]), patch.object(net.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), patch.object(net.OPENER, "open", side_effect=open_request), patch.object(canvas, "public_https"):
            client = canvas.Canvas(request_interval=5)
            self.assertEqual(client.courses(), [])
            self.assertEqual(client.file(42), ("answer.py", b"x=1"))
        self.assertEqual(starts, [0, 5, 10, 15, 20])

    def test_canvas_credentials_stop_subsequent_requests(self):
        failure = urllib.error.HTTPError("https://school.example/api/v1/files/42", 401, "unauthorized", {}, None)
        with patch.object(canvas, "settings", return_value={"canvas_base_url": "https://school.example", "canvas_token": "synthetic"}), patch.object(net.OPENER, "open", side_effect=failure) as connection:
            client = canvas.Canvas()
            for file_id in (42, 43):
                with self.assertRaises(net.RemoteError):
                    client.file(file_id)
        self.assertEqual(connection.call_count, 1)

    def test_download_requests_file_bytes_instead_of_json_self_redirect(self):
        urls = []
        download = "https://school.example/files/42/download?verifier=synthetic"
        def server(req, **kwargs):
            urls.append(req.full_url)
            if "/api/v1/" in req.full_url:
                return Response(json.dumps({"url": download, "filename": "answer.py", "size": 3}).encode())
            if req.full_url == download:
                location = download if req.get_header("Accept") == "application/json" else "https://cdn.example/answer.py"
                raise urllib.error.HTTPError(download, 302, "redirect", {"Location": location}, None)
            self.assertNotIn("Authorization", req.headers)
            return Response(b"x=1")
        with patch.object(canvas, "settings", return_value={"canvas_base_url": "https://school.example", "canvas_token": "synthetic"}), patch.object(net.OPENER, "open", side_effect=server), patch.object(canvas, "public_https"):
            self.assertEqual(canvas.Canvas().file(42), ("answer.py", b"x=1"))
        self.assertEqual(len(urls), 3)

    def test_download_self_redirect_stops_without_repeating_request(self):
        download = "https://school.example/files/42/download"
        with patch.object(canvas, "settings", return_value={"canvas_base_url": "https://school.example", "canvas_token": "synthetic"}), patch.object(canvas, "public_https"), patch.object(canvas, "json_request", return_value=({"url": download, "filename": "answer.py"}, {})), patch.object(canvas, "request", return_value=(None, {"redirect": download})) as connection:
            with self.assertRaises(net.RemoteError):
                canvas.Canvas().file(42)
        self.assertEqual(connection.call_count, 1)

    def test_self_redirect_uses_authorized_preview_url_with_submission_context(self):
        download = "https://school.example/files/42/download?verifier=synthetic"
        received = []
        def server(req, **kwargs):
            received.append(req.full_url)
            if req.full_url.endswith("/api/v1/files/42"):
                return Response(json.dumps({"url": download, "filename": "answer.py", "size": 3}).encode())
            if "/public_url" in req.full_url:
                self.assertTrue(req.full_url.endswith("?submission_id=7"))
                return Response(b'{"public_url":"https://cdn.example/answer.py"}')
            if req.full_url == download:
                raise urllib.error.HTTPError(download, 302, "redirect", {"Location": download}, None)
            self.assertNotIn("Authorization", req.headers)
            return Response(b"x=1")
        with patch.object(canvas, "settings", return_value={"canvas_base_url": "https://school.example", "canvas_token": "synthetic"}), patch.object(canvas, "public_https"), patch.object(net.OPENER, "open", side_effect=server):
            self.assertEqual(canvas.Canvas().file(42, submission_id=7), ("answer.py", b"x=1"))
        self.assertEqual(len(received), 4)

    def test_wrong_size_download_is_not_given_to_model_as_submission(self):
        with patch.object(canvas, "settings", return_value={"canvas_base_url": "https://school.example", "canvas_token": "synthetic"}), patch.object(canvas, "public_https"), patch.object(canvas, "json_request", return_value=({"url": "https://cdn.example/answer.py", "filename": "answer.py", "size": 100}, {})), patch.object(canvas, "request", return_value=(b"login page", {})):
            with self.assertRaisesRegex(net.RemoteError, "大小"):
                canvas.Canvas().file(42)

    def test_saved_attachment_reused_without_download(self):
        class NoDownload:
            def file(self, file_id):
                raise AssertionError("already saved attachment must not be downloaded")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "42.py").write_bytes(b"x=1")
            records = [{"id": 42, "name": "answer.py", "stored_name": "42.py", "bytes": 3}]
            collected = []
            text, _ = materials.submission_materials(NoDownload(), {"attachments": [{"id": 42}]}, path, collected, reuse_records=records)
        self.assertIn("x=1", text)
        self.assertEqual(collected[0]["id"], 42)


RUBRIC = [{"id": "correct", "description": "正确性", "points": 10}]
GRADE = {"score": 10, "criteria": [{"id": "correct", "score": 10, "points": 10, "description": "正确性", "feedback": "满足要求", "evidence": "return 1"}], "feedback": "满足要求", "needs_review": False}


class FakeCanvas:
    def __init__(self):
        self.a = {"name": "合成作业", "points_possible": 10, "grading_type": "points", "rubric": RUBRIC}
        self.rows = [{"user_id": i, "user": {"name": f"示例 {i}"}, "attempt": 1, "submitted_at": "2026-01-01T00:00:00Z", "submission_type": "online_text_entry", "body": f"def answer{i}(): return 1", "score": None, "attachments": []} for i in range(1, 5)]

    def assignment(self, *args):
        return copy.deepcopy(self.a)

    def submissions(self, *args):
        return copy.deepcopy(self.rows)

    def submission(self, course, assignment, user):
        return copy.deepcopy(next(r for r in self.rows if r["user_id"] == user))

    def publish(self, *args):
        raise AssertionError("these batches must never write grades")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for p in (patch.object(workflow, "DATA", Path(self.temp.name)), patch.object(workflow, "selected_model", return_value={"name": "synthetic", "base_url": "https://model.example"}), patch.object(workflow, "grade", return_value=copy.deepcopy(GRADE))):
            p.start()
            self.addCleanup(p.stop)
        self.canvas = FakeCanvas()

    def make_run(self):
        return workflow.batch(1, 2, canvas=self.canvas, requirements="# 合成要求\n函数返回 1", workers=1, limit=0)

    def test_resume_preserves_reviews_and_retries_only_failed_or_missing_downloads(self):
        run = self.make_run()
        workflow.approve(run["id"], 1, 9, "教师审核评语", "人工核查后调整")
        run = workflow.load_run(run["id"])
        approved = copy.deepcopy(run["results"][0])
        run["results"][1].update(status="manual_review", warnings=["真实内容异常"], manual_review_reason="需教师核查")
        anomaly = copy.deepcopy(run["results"][1])
        run["results"][2].update(status="error", error="附件下载失败：HTTP 401")
        run["results"][3].update(status="manual_review", warnings=["附件 42 读取失败：RemoteError"])
        workflow.save_json(workflow.run_path(run["id"]), run)
        result = workflow.resume(run["id"], canvas=self.canvas, workers=1)
        self.assertEqual(result["id"], run["id"])
        self.assertEqual(result["results"][0], approved)
        self.assertEqual(result["results"][1], anomaly)
        self.assertEqual([r["status"] for r in result["results"]], ["approved", "manual_review", "draft", "draft"])
        self.assertEqual(result["retried"], 2)

    def test_resume_refuses_changed_assignment_before_regrading(self):
        run = self.make_run()
        run["results"][0].update(status="error", error="download")
        workflow.save_json(workflow.run_path(run["id"]), run)
        self.canvas.a["points_possible"] = 20
        with self.assertRaisesRegex(ValueError, "变化"):
            workflow.resume(run["id"], canvas=self.canvas)
        self.assertEqual(workflow.load_run(run["id"]), run)

    def test_resume_changed_submission_requires_new_batch(self):
        run = self.make_run()
        run["results"][0].update(status="error", error="download")
        workflow.save_json(workflow.run_path(run["id"]), run)
        self.canvas.rows[0]["attempt"] = 2
        result = workflow.resume(run["id"], canvas=self.canvas, workers=1)
        self.assertEqual(result["retried"], 0)
        self.assertEqual(result["results"][0]["status"], "error")
        self.assertIn("变化", result["results"][0]["error"])

    def test_canvas_access_failure_pauses_before_other_students(self):
        self.canvas.rows[0].update(body="", submission_type="online_upload", attachments=[{"id": 42}])
        attempted = []
        def fail(file_id):
            attempted.append(file_id)
            raise canvas.CanvasAccessError("Canvas HTTP 401", status_code=401)
        self.canvas.file = fail
        run = self.make_run()
        self.assertEqual(run["status"], "paused")
        self.assertEqual(len(run["results"]), 1)
        self.assertEqual(attempted, [42])
        self.assertEqual(workflow.grade.call_count, 0)
        self.assertNotIn("score", run["results"][0])

    def test_running_batch_cannot_publish(self):
        run = self.make_run()
        workflow.approve(run["id"], 1, 10, "教师审核评语")
        run = workflow.load_run(run["id"])
        run["status"] = "running"
        workflow.save_json(workflow.run_path(run["id"]), run)
        with self.assertRaisesRegex(ValueError, "运行"):
            workflow.publish(run["id"], canvas=self.canvas)

    def test_interrupted_grading_stops_before_next_student_and_saves_pause(self):
        with patch.object(workflow, "grade", side_effect=KeyboardInterrupt) as grade:
            with self.assertRaises(KeyboardInterrupt):
                self.make_run()
        saved = json.loads(next((Path(self.temp.name) / "runs").glob("*.json")).read_text())
        self.assertEqual(saved["status"], "paused")
        self.assertEqual(grade.call_count, 1)

    def test_overlapping_resume_is_refused_without_network(self):
        run = self.make_run()
        class NoNetwork:
            def assignment(self, *args):
                raise AssertionError("must reject overlap before network access")
        with workflow.processing(run["id"]), self.assertRaisesRegex(ValueError, "运行"):
            workflow.resume(run["id"], canvas=NoNetwork())


if __name__ == "__main__":
    unittest.main()
