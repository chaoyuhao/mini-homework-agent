import concurrent.futures
import contextlib
import fcntl
import hashlib
import json
import math
import re
import threading
import time
import uuid

from .canvas import Canvas, CanvasAccessError
from .config import DATA, save_json, selected_model
from .llm import grade
from .materials import plain_html, submission_materials
from .net import RequestPacer
from .requirements import apply_policy, grading_policy, internal_reason as derive_internal_reason, markdown_rubric, score_in, student_feedback, validate_requirements

LOCK = threading.RLock()


@contextlib.contextmanager
def locked():
    """Serialize review/writeback across both the web app and CLI processes."""
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK, (DATA / ".review.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def snapshot(submission):
    return digest({key: submission.get(key) for key in ("attempt", "submitted_at", "body", "url", "score", "graded_at", "excused")} | {"file_ids": sorted(a["id"] for a in submission.get("attachments", []))})


def rubric_for(assignment, custom=None, requirements=""):
    maximum = assignment.get("points_possible")
    if isinstance(maximum, bool) or not isinstance(maximum, (int, float)) or not math.isfinite(maximum) or maximum <= 0:
        raise ValueError("作业满分必须为正数")
    explicit = markdown_rubric(requirements) if custom is None else None
    source = "custom" if custom is not None else ("markdown" if explicit else "canvas")
    raw = custom if custom is not None else (explicit or assignment.get("rubric"))
    if not raw:
        source = "provisional"
        # Visible, provisional criteria only; human approval is always required.
        raw = [{"id": "correct", "description": "功能与题目要求符合程度", "points": maximum * .6}, {"id": "edge", "description": "题目要求的边界情况与逻辑完整性", "points": maximum * .25}, {"id": "readable", "description": "代码结构与可读性", "points": maximum * .15}]
    if not isinstance(raw, list) or not raw or len(raw) > 30:
        raise ValueError("评分标准必须是含 1–30 项的 JSON 数组")
    rows = []
    for i, r in enumerate(raw):
        points = r.get("points")
        if isinstance(points, bool) or not isinstance(points, (int, float)) or not math.isfinite(points) or points < 0:
            raise ValueError("评分项 points 必须为非负有限数值")
        description = r.get("description", "")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("评分项必须包含 description")
        row = {"id": str(r.get("id", i + 1)), "description": description, "points": points}
        if r.get("long_description"):
            row["long_description"] = r["long_description"]
        if r.get("ratings"):
            row["ratings"] = r["ratings"]
        rows.append(row)
    if len(set(r["id"] for r in rows)) != len(rows):
        raise ValueError("评分项 ID 不能重复")
    if not math.isclose(sum(r["points"] for r in rows), maximum, abs_tol=.001):
        raise ValueError("评分项总分与作业满分不一致，请提供与满分一致的自定义标准")
    return rows, source


def run_path(run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("无效批次 ID")
    return DATA / "runs" / (run_id + ".json")


def load_run(run_id):
    return json.loads(run_path(run_id).read_text())


def runs():
    result = []
    for path in sorted((DATA / "runs").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        row = json.loads(path.read_text())
        result.append({k: row[k] for k in ("id", "title", "created_at", "model", "course_id", "assignment_id", "status")})
    return result


@contextlib.contextmanager
def processing(run_id):
    """A crashed process releases this lock; a second live resume cannot overlap."""
    path = run_path(run_id).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("此批次正在运行，请等待当前任务结束") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def batch(course_id, assignment_id, *, requirements="", limit=5, workers=1, request_interval=5, model_name=None, custom_rubric=None, notes="", include_graded=False, canvas=None, progress=None):
    requirements = validate_requirements(requirements)
    RequestPacer(request_interval)
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 8:
        raise ValueError("并行数量必须为 1–8")
    canvas = canvas or Canvas(request_interval=request_interval)
    assignment = canvas.assignment(course_id, assignment_id)
    if assignment.get("group_category_id"):
        raise ValueError("第一版不支持小组作业，请按小组人工评阅，避免重复或错误回写")
    if assignment.get("grading_type") not in ("points", "percent", None):
        raise ValueError("第一版仅支持数值分数或百分比作业")
    rubric, source = rubric_for(assignment, custom_rubric, requirements)
    policy = grading_policy(requirements)
    description, assignment_warnings = plain_html(assignment.get("description") or ""), []
    model = selected_model(model_name)
    assignment_data = {"title": assignment["name"], "canvas_description_context": description, "instructor_markdown": requirements, "grading_policy": policy, "points_possible": assignment["points_possible"], "instructor_notes": notes}
    rows = canvas.submissions(course_id, assignment_id)
    candidates = [s for s in rows if not s.get("excused") and s.get("submitted_at") and s.get("submission_type") in ("online_text_entry", "online_upload") and (include_graded or s.get("score") is None)]
    candidates.sort(key=lambda s: s["user_id"])
    if limit:
        candidates = candidates[:limit]
    run = {"id": uuid.uuid4().hex, "course_id": int(course_id), "assignment_id": int(assignment_id), "title": assignment["name"], "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "points_possible": assignment["points_possible"], "model": model["name"], "rubric": rubric, "rubric_source": source, "assignment": assignment_data, "assignment_guard": digest({k: assignment.get(k) for k in ("name", "description", "points_possible", "rubric", "updated_at")}), "status": "running", "warnings": assignment_warnings, "total": len(candidates), "skipped": len(rows) - len(candidates), "results": []}
    run["submission_ids"] = [s["user_id"] for s in candidates]
    run["submission_snapshots"] = {str(s["user_id"]): snapshot(s) for s in candidates}
    with processing(run["id"]):
        return _execute(run, candidates, canvas, model, workers, request_interval, progress)


def _execute(run, candidates, canvas, model, workers, request_interval, progress):
    assignment_data, rubric = run["assignment"], run["rubric"]
    policy, source = assignment_data["grading_policy"], run["rubric_source"]
    assignment_warnings = run["warnings"]
    prior = {r["user_id"]: r for r in run["results"]}
    stop = threading.Event()
    run.update(status="running", processing={"workers": workers, "request_interval": request_interval, "model": model["name"]}, progress_completed=0, progress_total=len(candidates))
    run.pop("blocked_reason", None)
    save_json(run_path(run["id"]), run)
    if progress:
        progress(run)

    def one(sub):
        if stop.is_set():
            return None
        item = {"user_id": sub["user_id"], "student": sub.get("user", {}).get("name", str(sub["user_id"])), "attempt": sub.get("attempt"), "submitted_at": sub.get("submitted_at"), "previous_score": sub.get("score"), "snapshot": snapshot(sub), "status": "draft", "warnings": list(assignment_warnings), "late": bool(sub.get("late"))}
        try:
            item["attachments"] = []
            content, warnings = submission_materials(canvas, sub, DATA / "attachments" / run["id"] / str(sub["user_id"]), item["attachments"], reuse_records=prior.get(sub["user_id"], {}).get("attachments"))
            item["warnings"].extend(warnings)
            item["materials"] = content
            if not content.strip():
                raise ValueError("没有可读取的提交内容，跳过自动评分")
            grade_assignment = {**assignment_data, "material_warnings": [re.sub(r"附件 \d+", "某个附件", w) for w in warnings]}
            key = digest({"version": 3, "model": model["name"], "endpoint": model["base_url"], "assignment": grade_assignment, "rubric": rubric, "content": content})
            cache = DATA / "cache" / (key + ".json")
            if cache.exists():
                result = json.loads(cache.read_text())
                item["cached"] = True
            else:
                result = grade(model, grade_assignment, rubric, content)
                save_json(cache, result)
                item["cached"] = False
            item.update(result)
            item["model"] = model["name"]
            item["needs_review"] = result["needs_review"] or bool(item["warnings"]) or source == "provisional"
            item.update(apply_policy(result, policy, run["points_possible"], late=item["late"]))
            if any(any(term in w for term in ("读取失败", "无法读取", "仅尝试", "超过", "截断")) for w in warnings):
                item.update(status="manual_review", final_score=None, needs_review=True, manual_review_reason="附件未完整读取，请对照原件人工评分")
        except Exception as e:
            item["status"] = "error"
            item["error"] = str(e) if isinstance(e, (ValueError, RuntimeError)) else type(e).__name__
            if isinstance(e, CanvasAccessError):
                item["canvas_access_error"] = True
                stop.set()
            if prior.get(sub["user_id"]):
                # Failed recovery must not hide originals already available for review.
                originals = {a["id"]: a for a in prior[sub["user_id"]].get("attachments", [])}
                originals.update({a["id"]: a for a in item.get("attachments", [])})
                item["attachments"] = list(originals.values())
            if hasattr(e, "trajectory"):
                item["trajectory"] = e.trajectory
        except BaseException:
            stop.set()
            raise
        return item
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(one, sub) for sub in candidates]
            try:
                for future in concurrent.futures.as_completed(futures):
                    item = future.result()
                    if item is None:
                        continue
                    prior[item["user_id"]] = item
                    run["results"] = sorted(prior.values(), key=lambda r: r["user_id"])
                    run["progress_completed"] += 1
                    if item.get("canvas_access_error"):
                        run["blocked_reason"] = item["error"]
                    save_json(run_path(run["id"]), run)
                    if progress:
                        progress(run)
            except BaseException:
                stop.set()
                for future in futures:
                    future.cancel()
                raise
    except BaseException:
        stop.set()
        raise
    finally:
        run["status"] = "paused" if stop.is_set() else "complete"
        save_json(run_path(run["id"]), run)
    return run


def retryable(row):
    if row["status"] == "error":
        return True
    # Older batches represented failed downloads as warnings on partial materials.
    return row["status"] in ("draft", "manual_review") and any(any(term in warning for term in ("RemoteError", "附件下载失败", "HTTP 401", "HTTP 403")) for warning in row.get("warnings", []))


def resume(run_id, *, workers=1, request_interval=5, model_name=None, canvas=None, progress=None):
    RequestPacer(request_interval)
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 8:
        raise ValueError("并行数量必须为 1–8")
    with processing(run_id):
        run = load_run(run_id)
        if run.get("demo") or run["status"] == "superseded":
            raise ValueError("演示或已替代的批次不能续跑")
        validate_requirements(run["assignment"]["instructor_markdown"])
        canvas = canvas or Canvas(request_interval=request_interval)
        assignment = canvas.assignment(run["course_id"], run["assignment_id"])
        guard = digest({k: assignment.get(k) for k in ("name", "description", "points_possible", "rubric", "updated_at")})
        if guard != run["assignment_guard"]:
            raise ValueError("题目或评分标准已变化，请另建批次")
        submissions = {s["user_id"]: s for s in canvas.submissions(run["course_id"], run["assignment_id"])}
        model = selected_model(model_name or run["model"])
        with locked():
            # Re-read reviews made while the read-only preflight was in progress.
            run = load_run(run_id)
            prior = {r["user_id"]: r for r in run["results"]}
            ids = run.get("submission_ids", sorted(prior))
            candidates = []
            for user_id in ids:
                row = prior.get(user_id)
                if row and not retryable(row):
                    continue
                sub = submissions.get(user_id)
                previous = run.get("submission_snapshots", {}).get(str(user_id), (row or {}).get("snapshot"))
                if not sub or not previous or snapshot(sub) != previous:
                    if row:
                        row.update(status="error", final_score=None, needs_review=True, error="提交版本或平台成绩已变化，请另建批次；保留原记录")
                    continue
                if sub.get("excused") or not sub.get("submitted_at") or sub.get("submission_type") not in ("online_text_entry", "online_upload"):
                    continue
                candidates.append(sub)
            run["retried"] = len(candidates)
            run["status"] = "running"
            save_json(run_path(run_id), run)
        return _execute(run, candidates, canvas, model, workers, request_interval, progress)


def content_score(row, score):
    if score == row.get("final_score"):
        if row.get("feedback_content_score") is not None:
            return row["feedback_content_score"]
        if row.get("status") in ("draft", "manual_review") and row.get("policy_score") is not None:
            return row["policy_score"]
    multiplier = row.get("late_multiplier_applied", 1)
    if row.get("late") and isinstance(multiplier, (int, float)) and not isinstance(multiplier, bool) and 0 < multiplier <= 1:
        return round(score / multiplier, 2)
    return score


def reviewed_feedback(row, score, feedback, policy):
    if not isinstance(feedback, str) or len(feedback) > 10000:
        raise ValueError("评语必须是文本，可以为空，且不超过 10000 字")
    base = content_score(row, score)
    text = student_feedback(feedback, base, policy)
    if score_in(base, policy.get("student_feedback_scores", [])) and not text:
        raise ValueError("此分数需要指出作业具体问题的学生评语")
    if len(text) > policy.get("feedback_max_chars", 10000):
        raise ValueError("学生评语超过教师指定的字数")
    return text


def approve(run_id, user_id, score, feedback="", reviewer_note="", internal_reason=None):
    with locked():
        run = load_run(run_id)
        if run["status"] not in ("complete", "paused"):
            raise ValueError("批次尚未完成")
        row = next(r for r in run["results"] if r["user_id"] == int(user_id))
        if row["status"] not in ("draft", "approved", "manual_review"):
            raise ValueError("此结果不能审核：" + row["status"])
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= run["points_possible"]:
            raise ValueError("审核分数超出范围")
        policy = run.get("assignment", {}).get("grading_policy", {})
        feedback = reviewed_feedback(row, score, feedback, policy)
        if (score != row.get("final_score") or row["status"] == "manual_review") and not reviewer_note.strip():
            raise ValueError("调整分数时请填写修改原因")
        reason = derive_internal_reason(row) if internal_reason is None else internal_reason
        if not isinstance(reason, str) or len(reason) > 10000:
            raise ValueError("内部评分原因必须为不超过 10000 字的文本")
        if score != row.get("final_score") or row["status"] == "manual_review":
            reason = reviewer_note.strip()
        if score_in(content_score(row, score), policy.get("internal_reason_required_scores", [])) and not reason.strip():
            raise ValueError("此分数需要内部评分原因；不会发送给学生")
        row.update(status="approved", final_score=score, feedback_content_score=content_score(row, score), final_feedback=feedback, internal_reason=reason.strip(), reviewer_note=reviewer_note, reviewed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        save_json(run_path(run_id), run)
        return row


def publish(run_id, *, canvas=None):
    canvas = canvas or Canvas()
    with locked():
        run = load_run(run_id)
        if run.get("demo"):
            raise ValueError("演示批次不能回写平台")
        if run["status"] not in ("complete", "paused"):
            raise ValueError("批次正在运行或已替代，不能回写")
        assignment = canvas.assignment(run["course_id"], run["assignment_id"])
        guard = digest({k: assignment.get(k) for k in ("name", "description", "points_possible", "rubric", "updated_at")})
        if guard != run["assignment_guard"]:
            raise ValueError("题目或评分标准已变化，请重新生成批改预览")
        for row in run["results"]:
            if row["status"] != "approved":
                continue
            current = canvas.submission(run["course_id"], run["assignment_id"], row["user_id"])
            if snapshot(current) != row["snapshot"]:
                row["publish_error"] = "提交版本或平台成绩已变化，请重新批改和审核"
                save_json(run_path(run_id), run)
                continue
            try:
                comment = reviewed_feedback(row, row["final_score"], row.get("final_feedback", ""), run.get("assignment", {}).get("grading_policy", {}))
            except ValueError as e:
                row["publish_error"] = str(e)
                save_json(run_path(run_id), run)
                continue
            row["final_feedback"] = comment
            row["internal_reason"] = derive_internal_reason(row)
            # Only the explicitly reviewed student comment crosses this boundary.
            # Technical audit markers and internal reasons remain local.
            row["publish_with_comment"] = bool(comment)
            row["publish_marker"] = None
            row["status"] = "publishing"
            save_json(run_path(run_id), run)
            try:
                response = canvas.publish(run["course_id"], run["assignment_id"], row["user_id"], row["final_score"], comment)
                # Late policies may deduct points; retain Canvas's actual score.
                row.update(status="published", canvas_score=response.get("score"), published_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            except Exception as e:
                row.update(status="publish_unknown", publish_error="请求未确认；先检查平台或运行 reconcile，禁止直接重发。" + (str(e) if isinstance(e, RuntimeError) else type(e).__name__))
            save_json(run_path(run_id), run)
        return run


def reconcile(run_id, *, canvas=None):
    canvas = canvas or Canvas()
    with locked():
        run = load_run(run_id)
        if run.get("demo"):
            raise ValueError("演示批次无需核对平台")
        for row in run["results"]:
            if row["status"] not in ("publishing", "publish_unknown"):
                continue
            current = canvas.submission(run["course_id"], run["assignment_id"], row["user_id"])
            if "publish_marker" in row and row["publish_marker"] is None:
                row["canvas_score"] = current.get("score")
                row["publish_error"] = "本次未发送程序标记，无法自动确认请求是否成功。请人工核对平台成绩与评语；保持待核对状态，不自动重发。"
                continue
            marker = f"[elearning-grader:{run_id}:{row['user_id']}]"
            comments = current.get("submission_comments") or []
            if any(marker in c.get("comment", "") for c in comments):
                row.update(status="published", canvas_score=current.get("score"), reconciliation="已在平台找到本批次评语标记；请核对当前成绩")
            else:
                row["publish_error"] = "平台尚未查到本批次评语；保持未知状态，请人工检查后另建批次"
        save_json(run_path(run_id), run)
        return run


def demo(offline=False):
    from .llm import BENCH_RUBRIC
    from .config import selected_model
    model = {"name": "offline-demo"} if offline else selected_model()
    assignment = {"title": "演示 · 偶数求和", "instructor_markdown": "实现 sum_even(numbers)，返回偶数之和，支持空列表和负数。", "points_possible": 10}
    run = {"id": uuid.uuid4().hex, "demo": True, "course_id": 0, "assignment_id": 0, "title": assignment["title"], "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": model["name"], "points_possible": 10, "rubric": BENCH_RUBRIC, "rubric_source": "custom", "assignment": assignment, "status": "complete", "warnings": [], "total": 2, "skipped": 0, "results": []}
    for i, code in enumerate(("def sum_even(numbers):\n    return sum(n for n in numbers if n % 2 == 0)", "def sum_even(numbers):\n    return sum(n for n in numbers if n % 2 == 1)")):
        if offline:
            from .agent import OfflineDemoModel, run_grader
            result = run_grader(model, assignment, BENCH_RUBRIC, code, model_adapter=OfflineDemoModel(model))
        else:
            result = grade(model, assignment, BENCH_RUBRIC, code)
        run["results"].append({**result, "user_id": i + 1, "student": f"演示学生 {i + 1}", "status": "draft", "attempt": 1, "materials": code, "warnings": [], "final_score": result["score"], "final_feedback": result["feedback"]})
    save_json(run_path(run["id"]), run)
    return run


def grade_local(requirements_path, submission_path, *, title="本地作业", points=100, model_name=None, custom_rubric=None):
    from .materials import extract
    requirements = validate_requirements(requirements_path.read_text(encoding="utf-8-sig"))
    if submission_path.stat().st_size > 10_000_000:
        raise ValueError("本地提交文件超过 10 MB")
    materials, warnings = extract(submission_path.name, submission_path.read_bytes())
    if not materials.strip():
        raise ValueError("本地提交没有可读取内容")
    policy = grading_policy(requirements)
    assignment = {"title": title, "instructor_markdown": requirements, "grading_policy": policy, "points_possible": points, "material_warnings": warnings}
    rubric, source = rubric_for({"points_possible": points}, custom_rubric, requirements)
    model = selected_model(model_name)
    result = grade(model, assignment, rubric, materials)
    row = {**result, "user_id": 1, "student": "本地提交", "status": "draft", "attempt": 1, "materials": materials, "warnings": warnings, "needs_review": result["needs_review"] or bool(warnings) or source == "provisional", "final_score": result["score"], "final_feedback": result["feedback"]}
    row.update(apply_policy(result, policy, points))
    run = {"id": uuid.uuid4().hex, "demo": True, "local": True, "course_id": 0, "assignment_id": 0, "title": title, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": model["name"], "points_possible": points, "rubric": rubric, "rubric_source": source, "assignment": assignment, "status": "complete", "warnings": warnings, "total": 1, "skipped": 0, "results": [row]}
    save_json(run_path(run["id"]), run)
    return run
