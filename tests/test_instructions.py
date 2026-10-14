import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grader import agent, llm, requirements, workflow
from grader import __main__ as cli
from grader.net import RemoteError


TASKS = """# 两题作业

## 第1题：选择结构
实现 choose(age)，年龄大于 18 时返回 True。

## 第2题：定积分
计算 f(x)=x*x 在 [0, 1] 的定积分，方法不限。

# 本次评分方式
评分方式：整体评分
两题均完成即可满分，不为两题推定分值。
"""
RULES = """# 通用规则
正常工作量，无致命缺陷，尽量给95以上。
缺的东西太多，给90。
迟交成绩为80%。评论50字以内。
只有90分的作业才有评语。
95分的作业内部保留原因。
异常人工审查。
"""


def complete_grade(rubric, questions):
    return {
        "criteria": [{"id": r["id"], "score": r["points"], "feedback": "要求已完成", "evidence": "提交中的实现"} for r in rubric],
        "question_checks": [{"id": q["id"], "status": "met", "feedback": "本题要求已完成", "evidence": q["title"] + "对应代码"} for q in questions],
        "feedback": "", "internal_reason": "两题均有对应实现", "confidence": .95,
        "needs_review": False, "assessment": "normal", "assessment_reason": "两题均有实现与依据",
    }


class InstructionTests(unittest.TestCase):
    def test_questions_keep_their_own_requirements_only(self):
        questions = requirements.markdown_questions(TASKS)
        self.assertEqual([q["id"] for q in questions], ["q_1", "q_2"])
        self.assertIn("choose(age)", questions[0]["requirements"])
        self.assertNotIn("定积分", questions[0]["requirements"])
        self.assertNotIn("整体评分", questions[1]["requirements"])

    def test_nested_headings_remain_in_the_question(self):
        questions = requirements.markdown_questions("## Question 2: Integrate\nCompute an integral.\n### Notes\nSigned area.\n## General rules\nKeep comments short.")
        self.assertEqual(questions[0]["id"], "q_2")
        self.assertIn("Signed area", questions[0]["requirements"])
        self.assertNotIn("General rules", questions[0]["requirements"])

    def test_duplicate_question_numbers_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "题号"):
            requirements.markdown_questions("## 第1题\nA\n## 第1题\nB")

    def test_ordinary_numbered_lists_are_not_mistaken_for_questions(self):
        self.assertEqual(requirements.markdown_questions("# 作业\n1. 命名清晰\n2. 注释有效"), [])

    def test_question_like_code_comments_are_not_headings(self):
        text = "## 第1题\n实现函数。\n```python\n## 第2题\nx = 1\n```\n## 第3题\n另一题。"
        questions = requirements.markdown_questions(text)
        self.assertEqual([q["id"] for q in questions], ["q_1", "q_3"])
        self.assertIn("x = 1", questions[0]["requirements"])

    def test_shared_rules_and_task_details_are_kept_separate(self):
        bundle = requirements.instruction_bundle(TASKS, RULES, maximum=100)
        self.assertEqual(bundle["instructor_markdown"], TASKS.strip())
        self.assertEqual(bundle["instructor_policy_markdown"], RULES.strip())
        self.assertEqual(bundle["grading_policy"]["student_feedback_scores"], [90])
        self.assertEqual(len(bundle["questions"]), 2)

    def test_explicit_task_rule_overrides_shared_baseline(self):
        bundle = requirements.instruction_bundle(TASKS + "\n迟交成绩为50%。", RULES, maximum=100)
        self.assertEqual(bundle["grading_policy"]["late_multiplier"], .5)
        self.assertEqual(bundle["grading_policy"]["normal_min_score"], 95)

    def test_legacy_single_document_policy_still_works(self):
        bundle = requirements.instruction_bundle(TASKS + RULES, maximum=100)
        self.assertEqual(bundle["instructor_policy_markdown"], "")
        self.assertEqual(bundle["grading_policy"]["insufficient_score"], 90)

    def test_internal_reason_score_does_not_cross_another_score_clause(self):
        policy = requirements.grading_policy("全部达标时给100分；仍有一处实质缺口时给95分并内部保留原因。")
        self.assertEqual(policy["internal_reason_required_scores"], [95])

    def test_combined_instructions_cannot_be_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, "60000"):
            requirements.instruction_bundle("x" * 30001, "y" * 30000)

    def test_policy_scores_must_fit_assignment_maximum(self):
        with self.assertRaisesRegex(ValueError, "满分"):
            requirements.instruction_bundle(TASKS, RULES, maximum=10)

    def test_holistic_rubric_does_not_invent_question_weights(self):
        rubric, source = workflow.rubric_for({"points_possible": 100}, requirements=TASKS)
        self.assertEqual(source, "holistic")
        self.assertEqual([(r["id"], r["points"]) for r in rubric], [("overall", 100)])

    def test_explicit_holistic_mode_overrides_platform_dimensions(self):
        assignment = {"points_possible": 100, "rubric": [{"id": "legacy", "description": "平台维度", "points": 100}]}
        rubric, source = workflow.rubric_for(assignment, requirements=TASKS)
        self.assertEqual(source, "holistic")
        self.assertEqual(rubric[0]["id"], "overall")

    def test_explicit_question_points_still_form_a_rubric(self):
        text = TASKS + "\n| 题目 | 分值 | 要求 |\n| --- | --- | --- |\n| 第1题 | 30 | 选择结构 |\n| 第2题 | 70 | 积分 |"
        rubric, source = workflow.rubric_for({"points_possible": 100}, requirements=text)
        self.assertEqual(source, "markdown")
        self.assertEqual([r["points"] for r in rubric], [30, 70])


class QuestionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.questions = [{"id": "q_1", "title": "选择结构"}, {"id": "q_2", "title": "积分"}]
        self.rubric = [{"id": "overall", "description": "整体完成度", "points": 100}]
        self.value = complete_grade(self.rubric, self.questions)

    def test_every_question_needs_a_status_and_evidence(self):
        for checks in (None, self.value["question_checks"][:1], [self.value["question_checks"][0]] * 2):
            with self.assertRaises(ValueError):
                llm.validate_grade({**self.value, "question_checks": checks}, self.rubric, questions=self.questions)

    def test_invalid_status_or_empty_evidence_is_rejected(self):
        for field, value in [("status", "perfect"), ("evidence", ""), ("feedback", "")]:
            result = copy.deepcopy(self.value)
            result["question_checks"][1][field] = value
            with self.assertRaises(ValueError):
                llm.validate_grade(result, self.rubric, questions=self.questions)

    def test_question_titles_come_from_teacher_and_have_no_numeric_weights(self):
        self.value["question_checks"][0].update(title="模型自拟题目", points=50, score=50)
        result = llm.validate_grade(self.value, self.rubric, questions=self.questions)
        self.assertEqual(result["question_checks"][0]["title"], "选择结构")
        self.assertNotIn("points", result["question_checks"][0])
        self.assertNotIn("score", result["question_checks"][0])

    def test_uncertain_question_withholds_final_score(self):
        self.value["question_checks"][1].update(status="uncertain", feedback="图片中积分代码无法辨认")
        result = llm.validate_grade(self.value, self.rubric, questions=self.questions)
        self.assertTrue(result["needs_review"])
        final = requirements.apply_policy(result, {}, 100)
        self.assertEqual(final["status"], "manual_review")
        self.assertIsNone(final["final_score"])

    def test_agent_repairs_missing_question_check_before_accepting_grade(self):
        env = agent.HomeworkEnvironment(self.rubric, "original code", {}, self.questions)
        bad = {**self.value, "question_checks": self.value["question_checks"][:1]}
        self.assertIn("error", env.execute({"tool": "submit_grade", "grade": bad}))
        self.assertTrue(env.execute({"tool": "submit_grade", "grade": self.value})["complete"])

    def test_real_agent_loop_validates_question_checks_without_duplicating_text(self):
        assignment = requirements.instruction_bundle(TASKS)
        captured = []
        value = complete_grade(self.rubric, assignment["questions"])
        def query(messages):
            captured.append(json.loads(messages[-1]["content"]))
            return {"role": "assistant", "content": json.dumps(value), "extra": {"actions": [{"tool": "submit_grade", "grade": value}]}}
        with patch.object(agent.HomeworkModel, "query", side_effect=query):
            result = agent.run_grader({"name": "synthetic"}, assignment, self.rubric, "synthetic student code")
        self.assertEqual(len(result["question_checks"]), 2)
        self.assertEqual(result["score"], 100)
        self.assertEqual(captured[0]["assignment"]["instructor_markdown"], TASKS.strip())
        self.assertTrue(all(set(q) == {"id", "title"} for q in captured[0]["assignment"]["questions"]))


class BundleWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = {"name": "synthetic", "base_url": "https://model.example"}
        self.canvas = type("FakeCanvas", (), {
            "assignment": lambda *_: {"name": "两题", "points_possible": 100, "grading_type": "points"},
            "submissions": lambda *_: [{"user_id": 1, "attempt": 1, "submitted_at": "2026-01-01T00:00:00Z", "submission_type": "online_text_entry", "body": "<pre>synthetic code</pre>", "score": None, "late": True}],
        })()
        self.patches = [patch.object(workflow, "DATA", self.root), patch.object(workflow, "selected_model", return_value=self.model), patch.object(workflow, "grade", side_effect=self.grade)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def grade(self, model, assignment, rubric, materials):
        value = complete_grade(rubric, assignment["questions"])
        value["criteria"][0]["score"] = 95
        value["question_checks"][0].update(status="gap", feedback="年龄 18 的分支有偏差", evidence="if age >= 18")
        value["internal_reason"] = "第一题某边界条件有偏差"
        result = llm.validate_grade(value, rubric, questions=assignment["questions"])
        result.update(assessment="normal", assessment_reason="两题均有实现")
        return result

    def test_batch_saves_resolved_policy_and_both_question_checks(self):
        run = workflow.batch(1, 2, requirements=TASKS, grading_rules=RULES, canvas=self.canvas, request_interval=0)
        self.assertEqual(run["rubric_source"], "holistic")
        self.assertEqual(run["results"][0]["final_score"], 76)
        self.assertEqual(run["results"][0]["final_feedback"], "")
        saved = workflow.load_run(run["id"])
        self.assertEqual(saved["assignment"]["instructor_policy_markdown"], RULES.strip())
        self.assertEqual(len(saved["results"][0]["question_checks"]), 2)
        self.assertEqual(saved["results"][0]["status"], "draft")

    def test_shared_rule_tables_do_not_become_assignment_dimensions(self):
        rules = RULES + "\n| 评分项 | 分值 |\n| --- | --- |\n| 旧作业维度 | 100 |"
        run = workflow.batch(1, 2, requirements=TASKS, grading_rules=rules, canvas=self.canvas, request_interval=0)
        self.assertEqual(run["rubric"][0]["id"], "overall")

    def test_local_grading_uses_separate_policy_file(self):
        task_file, rules_file, submission = [self.root / f for f in ("tasks.md", "policy.md", "work.py")]
        task_file.write_text(TASKS)
        rules_file.write_text(RULES)
        submission.write_text("x = 1")
        run = workflow.grade_local(task_file, submission, grading_rules=rules_file.read_text())
        self.assertEqual(run["assignment"]["instructor_policy_markdown"], RULES.strip())
        self.assertEqual(run["results"][0]["final_score"], 95)

    def test_missing_assignment_details_fail_even_with_shared_policy(self):
        with patch.object(workflow, "Canvas") as constructor, self.assertRaises(ValueError):
            workflow.batch(1, 2, grading_rules=RULES)
        constructor.assert_not_called()

    def test_invalid_shared_text_fails_before_constructing_canvas(self):
        with patch.object(workflow, "Canvas") as constructor, self.assertRaises(ValueError):
            workflow.batch(1, 2, requirements=TASKS, grading_rules=123)
        constructor.assert_not_called()

    def test_resume_uses_saved_policy_after_source_file_changes(self):
        rules_file = self.root / "policy.md"
        rules_file.write_text(RULES)
        workflow.grade.side_effect = RemoteError("synthetic temporary model error")
        run = workflow.batch(1, 2, requirements=TASKS, grading_rules=rules_file.read_text(), canvas=self.canvas, request_interval=0)
        self.assertEqual(run["results"][0]["status"], "error")
        rules_file.write_text("迟交成绩为50%。")
        workflow.grade.side_effect = self.grade
        resumed = workflow.resume(run["id"], canvas=self.canvas, request_interval=0)
        self.assertEqual(resumed["assignment"]["instructor_policy_markdown"], RULES.strip())
        self.assertEqual(resumed["results"][0]["final_score"], 76)

    def test_cli_forwards_policy_file_for_both_entry_points(self):
        task_file, rules_file, submission = [self.root / f for f in ("tasks.md", "policy.md", "work.py")]
        task_file.write_text(TASKS)
        rules_file.write_text(RULES)
        submission.write_text("x = 1")
        common = ["--requirements", str(task_file), "--policy", str(rules_file)]
        for command, args, target in [("grade", ["1", "2"], "batch"), ("grade-local", ["--submission", str(submission)], "grade_local")]:
            with patch("sys.argv", ["mini_homework_agent", command, *args, *common]), patch("sys.stdout", new=io.StringIO()), patch.object(workflow, target, return_value={}) as call:
                cli.main()
            self.assertEqual(call.call_args.kwargs["grading_rules"], RULES)
