"""Student comments and local grading reasons stay on separate paths."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grader import agent, canvas, llm, requirements, workflow

RUBRIC = [{"id": "correct", "description": "核心功能", "points": 100}]
GOOD = {"criteria": [{"id": "correct", "score": 100, "feedback": "核心功能达标", "evidence": "def work(x):"}], "feedback": "", "confidence": .9, "needs_review": False, "assessment": "normal", "assessment_reason": "材料完整"}
POLICY = {"normal_min_score": 95, "insufficient_score": 90, "feedback_max_chars": 50, "student_feedback_scores": [90], "internal_reason_required_scores": [95]}


class FeedbackPolicyTests(unittest.TestCase):
    def test_markdown_parses_only_90_comments_and_internal_95_reasons(self):
        policy = requirements.grading_policy("只有90分的作业才有评语。\n95分的作业内部保留原因，不给学生评语。")
        self.assertEqual(policy.get("student_feedback_scores"), [90])
        self.assertEqual(policy.get("internal_reason_required_scores"), [95])

    def test_empty_or_absent_student_feedback_keeps_valid_evidence(self):
        for feedback in ("", "  "):
            result = llm.validate_grade({**GOOD, "feedback": feedback}, RUBRIC)
            self.assertEqual(result["score"], 100)
            self.assertEqual(result["feedback"], "")
        no_feedback = copy.deepcopy(GOOD)
        no_feedback.pop("feedback")
        self.assertEqual(llm.validate_grade(no_feedback, RUBRIC)["feedback"], "")

    def test_empty_criterion_reason_still_fails(self):
        response = copy.deepcopy(GOOD)
        response["criteria"][0]["feedback"] = ""
        with self.assertRaises(ValueError):
            llm.validate_grade(response, RUBRIC)

    def test_95_reason_derived_from_deduction_and_not_sent_to_students(self):
        value = copy.deepcopy(GOOD)
        value["criteria"][0].update(score=95, feedback="未保留 work(x) 接口")
        value["feedback"] = "总体不错，可继续优化"
        score = llm.validate_grade(value, RUBRIC)
        result = requirements.apply_policy(score, POLICY, 100)
        self.assertEqual(result["final_score"], 95)
        self.assertEqual(result["final_feedback"], "")
        self.assertIn("未保留 work(x) 接口", result["internal_reason"])

    def test_policy_feedback_uses_content_score_before_late_deduction(self):
        value = llm.validate_grade({**GOOD, "feedback": "缺少 AI 交互记录"}, RUBRIC)
        value["assessment"] = "insufficient"
        result = requirements.apply_policy(value, {**POLICY, "late_multiplier": .8}, 100, late=True)
        self.assertEqual(result["final_score"], 72)
        self.assertEqual(result["final_feedback"], "缺少 AI 交互记录")
        value["assessment"] = "normal"
        value["score"] = 95
        result = requirements.apply_policy(value, {**POLICY, "late_multiplier": .8}, 100, late=True)
        self.assertEqual(result["final_score"], 76)
        self.assertEqual(result["final_feedback"], "")

    def test_agent_accepts_empty_100_feedback(self):
        env = agent.HomeworkEnvironment(RUBRIC, "def work(x): pass", POLICY)
        result = env.execute({"tool": "submit_grade", "grade": GOOD})
        self.assertTrue(result.get("complete"))

    def test_agent_requires_problem_feedback_at_90(self):
        env = agent.HomeworkEnvironment(RUBRIC, "def work(x): pass", POLICY)
        result = env.execute({"tool": "submit_grade", "grade": {**GOOD, "assessment": "insufficient"}})
        self.assertIn("error", result)

    def test_near_90_score_does_not_enable_a_student_comment(self):
        value = llm.validate_grade({**GOOD, "feedback": "不应公开的文字"}, RUBRIC)
        value["score"] = 90.01
        result = requirements.apply_policy(value, {"student_feedback_scores": [90]}, 100)
        self.assertEqual(result["final_feedback"], "")


class FakeCanvas:
    def __init__(self):
        self.a = {"name": "合成作业", "points_possible": 100, "grading_type": "points", "rubric": RUBRIC}
        self.s = {"user_id": 1, "attempt": 1, "submitted_at": "2026-01-01T00:00:00Z", "submission_type": "online_text_entry", "body": "def work(x): return x", "score": None, "attachments": [], "user": {"name": "示例"}}
        self.calls = []
        self.fail = False

    def assignment(self, *args):
        return copy.deepcopy(self.a)

    def submissions(self, *args):
        return [copy.deepcopy(self.s)]

    def submission(self, *args):
        return copy.deepcopy(self.s)

    def publish(self, course, assignment, user, score, comment):
        self.calls.append((score, comment))
        if self.fail:
            # The write happened, but the client did not receive its result.
            self.s.update(score=score, graded_at="2026-10-12T00:00:00Z")
            raise RuntimeError("timeout")
        self.s.update(score=score, graded_at="2026-10-12T00:00:00Z")
        return copy.deepcopy(self.s)


class FeedbackWorkflowTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for p in (patch.object(workflow, "DATA", Path(folder.name)), patch.object(workflow, "selected_model", return_value={"name": "synthetic", "base_url": "https://model.example"}), patch.object(workflow, "grade", return_value=llm.validate_grade(GOOD, RUBRIC))):
            p.start()
            self.addCleanup(p.stop)
        self.canvas = FakeCanvas()

    def make_run(self):
        return workflow.batch(1, 2, requirements="# 合成要求\n实现 work(x)", canvas=self.canvas)

    def test_empty_review_feedback_publishes_grade_without_marker_comment(self):
        run = self.make_run()
        workflow.approve(run["id"], 1, 100, "")
        result = workflow.publish(run["id"], canvas=self.canvas)
        self.assertEqual(self.canvas.calls, [(100, "")])
        self.assertEqual(result["results"][0]["status"], "published")

    def test_score_only_uncertain_write_keeps_unknown_even_if_score_matches(self):
        run = self.make_run()
        workflow.approve(run["id"], 1, 100, "")
        self.canvas.fail = True
        workflow.publish(run["id"], canvas=self.canvas)
        result = workflow.reconcile(run["id"], canvas=self.canvas)
        self.assertEqual(result["results"][0]["status"], "publish_unknown")
        self.assertEqual(len(self.canvas.calls), 1)

    def test_review_enforces_90_only_and_keeps_95_reason_local(self):
        run = self.make_run()
        run["assignment"]["grading_policy"] = POLICY
        row = run["results"][0]
        row.update(final_score=95, policy_score=95, internal_reason="未保留接口")
        workflow.save_json(workflow.run_path(run["id"]), run)
        reviewed = workflow.approve(run["id"], 1, 95, "这段不应发给学生")
        self.assertEqual(reviewed["final_feedback"], "")
        self.assertEqual(reviewed["internal_reason"], "未保留接口")
        workflow.publish(run["id"], canvas=self.canvas)
        self.assertEqual(self.canvas.calls, [(95, "")])

    def test_review_changed_to_90_requires_specific_comment(self):
        run = self.make_run()
        run["assignment"]["grading_policy"] = POLICY
        workflow.save_json(workflow.run_path(run["id"]), run)
        with self.assertRaises(ValueError):
            workflow.approve(run["id"], 1, 90, "", "缺少记录")
        reviewed = workflow.approve(run["id"], 1, 90, "缺少 AI 交互记录", "缺少记录")
        self.assertEqual(reviewed["final_feedback"], "缺少 AI 交互记录")

    def test_publish_filters_legacy_approved_feedback_for_95(self):
        run = self.make_run()
        run["assignment"]["grading_policy"] = POLICY
        run["results"][0].update(status="approved", final_score=95, policy_score=95, final_feedback="历史上保存的泛泛建议", internal_reason="内部原因")
        workflow.save_json(workflow.run_path(run["id"]), run)
        workflow.publish(run["id"], canvas=self.canvas)
        self.assertEqual(self.canvas.calls, [(95, "")])

    def test_90_publishes_only_reviewed_problem_text(self):
        run = self.make_run()
        run["assignment"]["grading_policy"] = POLICY
        workflow.save_json(workflow.run_path(run["id"]), run)
        workflow.approve(run["id"], 1, 90, "缺少 AI 交互记录", "内部审核：确认缺少记录")
        workflow.publish(run["id"], canvas=self.canvas)
        self.assertEqual(self.canvas.calls, [(90, "缺少 AI 交互记录")])

    def test_95_explicit_empty_internal_reason_is_rejected(self):
        run = self.make_run()
        run["assignment"]["grading_policy"] = POLICY
        run["results"][0].update(final_score=95, policy_score=95)
        workflow.save_json(workflow.run_path(run["id"]), run)
        with self.assertRaises(ValueError):
            workflow.approve(run["id"], 1, 95, "", internal_reason="")

    def test_late_content_score_90_review_keeps_comment_when_publishing_72(self):
        run = self.make_run()
        run["assignment"]["grading_policy"] = {**POLICY, "late_multiplier": .8}
        run["results"][0].update(final_score=72, policy_score=90, late=True, late_multiplier_applied=.8)
        workflow.save_json(workflow.run_path(run["id"]), run)
        workflow.approve(run["id"], 1, 72, "缺少过程记录")
        workflow.publish(run["id"], canvas=self.canvas)
        self.assertEqual(self.canvas.calls, [(72, "缺少过程记录")])


class CanvasPayloadTests(unittest.TestCase):
    def test_blank_comment_is_omitted_entirely_from_put_payload(self):
        with patch.object(canvas, "settings", return_value={"canvas_base_url": "https://school.example", "canvas_token": "synthetic"}):
            client = canvas.Canvas()
        with patch.object(client, "api", return_value={"score": 95}) as api:
            client.publish(1, 2, 3, 95, "  ")
        self.assertEqual(api.call_args.kwargs["payload"], {"submission": {"posted_grade": "95"}})

    def test_real_comment_kept_without_internal_reason_in_payload(self):
        with patch.object(canvas, "settings", return_value={"canvas_base_url": "https://school.example", "canvas_token": "synthetic"}):
            client = canvas.Canvas()
        with patch.object(client, "api", return_value={"score": 90}) as api:
            client.publish(1, 2, 3, 90, "缺少过程记录")
        self.assertEqual(api.call_args.kwargs["payload"], {"submission": {"posted_grade": "90"}, "comment": {"text_comment": "缺少过程记录"}})
