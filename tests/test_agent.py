import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grader import agent, config, net, requirements

MODEL = {"name": "test-model", "base_url": "https://model.example/v1", "api_key": "test-secret-value", "chat_template_kwargs": {}}
RUBRIC = [{"id": "correct", "description": "正确性", "points": 10}]
GOOD = {"criteria": [{"id": "correct", "score": 10, "feedback": "满足要求", "evidence": "return 1"}], "feedback": "符合题目要求", "confidence": .9, "needs_review": False}
ASSIGNMENT = {"title": "练习", "instructor_markdown": "实现函数，返回整数 1", "points_possible": 10}
MATERIALS = "def answer():\n    return 1"


def response(value):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value, ensure_ascii=False)}}]}, {}


class AgentTests(unittest.TestCase):
    def test_actual_mini_core_used_and_no_secrets_in_trajectory(self):
        with patch.object(agent, "json_request", return_value=response(GOOD)):
            result = agent.run_grader(MODEL, ASSIGNMENT, RUBRIC, MATERIALS)
        self.assertEqual(result["score"], 10)
        self.assertEqual(result["agent_steps"], 1)
        self.assertEqual(result["trajectory"]["info"]["mini_version"], "2.4.6")
        self.assertIn("HomeworkAgent", result["trajectory"]["info"]["config"]["agent_type"])
        self.assertNotIn(MODEL["api_key"], json.dumps(result))

    def test_empty_provider_specific_kwargs_not_sent(self):
        with patch.object(agent, "json_request", return_value=response(GOOD)) as call:
            agent.run_grader(MODEL, ASSIGNMENT, RUBRIC, MATERIALS)
        self.assertNotIn("chat_template_kwargs", call.call_args.kwargs["payload"])

    def test_bad_score_repaired_with_observation(self):
        bad = copy.deepcopy(GOOD)
        bad["criteria"][0]["score"] = 99
        with patch.object(agent, "json_request", side_effect=[response(bad), response(GOOD)]) as call:
            result = agent.run_grader(MODEL, ASSIGNMENT, RUBRIC, MATERIALS)
        self.assertEqual(result["agent_steps"], 2)
        sent = call.call_args.kwargs["payload"]["messages"]
        self.assertIn("分数越界", sent[-1]["content"])

    def test_ast_tool_never_executes_student_code(self):
        malicious = "raise SystemExit('never execute')"
        actions = [{"tool": "inspect_python", "snippet": malicious}, GOOD]
        with patch.object(agent, "json_request", side_effect=[response(x) for x in actions]):
            result = agent.run_grader(MODEL, ASSIGNMENT, RUBRIC, malicious)
        observations = [m for m in result["trajectory"]["messages"] if m["role"] == "user"]
        self.assertTrue(any('"executed": false' in m["content"] for m in observations))

    def test_fabricated_code_cannot_be_inspected(self):
        env = agent.HomeworkEnvironment(RUBRIC, MATERIALS)
        output = env.execute({"tool": "inspect_python", "snippet": "print('invented')"})
        self.assertIn("error", output)

    def test_no_shell_or_publish_tool(self):
        env = agent.HomeworkEnvironment(RUBRIC, MATERIALS)
        for tool in ("bash", "publish", "curl", "exec", "read_file"):
            self.assertIn("error", env.execute({"tool": tool, "command": "echo forbidden"}))

    def test_repeated_invalid_output_stops(self):
        raw = ({"choices": [{"message": {"content": "not JSON"}}]}, {})
        with patch.object(agent, "json_request", return_value=raw) as call:
            with self.assertRaises(agent.AgentGradingError) as caught:
                agent.run_grader(MODEL, ASSIGNMENT, RUBRIC, MATERIALS)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(caught.exception.trajectory["info"]["exit_status"], "RepeatedFormatError")

    def test_invalid_json_delimiters_get_specific_repair_instruction(self):
        bad = json.dumps(GOOD, ensure_ascii=False).replace('return 1"', 'return 1”')
        def provider(url, **kwargs):
            hint = kwargs["payload"]["messages"][-1]["content"]
            if "英文双引号" in hint:
                return response(GOOD)
            return {"choices": [{"finish_reason": "stop", "message": {"content": bad}}]}, {}
        with patch.object(agent, "json_request", side_effect=provider):
            result = agent.run_grader(MODEL, ASSIGNMENT, RUBRIC, MATERIALS)
        self.assertEqual(result["score"], 10)
        self.assertEqual(result["agent_steps"], 2)

    def test_never_valid_grade_hits_step_limit(self):
        bad = copy.deepcopy(GOOD)
        bad["criteria"][0]["score"] = 99
        with patch.object(agent, "json_request", return_value=response(bad)) as call:
            with self.assertRaises(agent.AgentGradingError) as caught:
                agent.run_grader(MODEL, ASSIGNMENT, RUBRIC, MATERIALS)
        self.assertEqual(call.call_count, 3)
        self.assertEqual(caught.exception.trajectory["info"]["exit_status"], "LimitsExceeded")

    def test_read_submission_is_bounded(self):
        env = agent.HomeworkEnvironment(RUBRIC, MATERIALS)
        output = env.execute({"tool": "read_submission", "offset": 0, "limit": 5})
        self.assertEqual(output["student_text_untrusted"], MATERIALS[:5])
        self.assertIn("error", env.execute({"tool": "read_submission", "limit": 100000}))


class MarkdownTests(unittest.TestCase):
    def test_explicit_table_parsed_without_inferred_weights(self):
        text = "| 评分项 | 分值 | 要求 |\n| --- | ---: | --- |\n| 功能 | 8 | 偶数求和 |\n| 可读性 | 2分 | 清晰 |\n| 合计 | 10 | |"
        rows = requirements.markdown_rubric(text)
        self.assertEqual(sum(r["points"] for r in rows), 10)
        self.assertIn("偶数求和", rows[0]["description"])

    def test_requirements_without_table_have_no_inferred_weights(self):
        self.assertIsNone(requirements.markdown_rubric("# 要求\n返回整数 1，保持代码可读。"))

    def test_malformed_points_not_silently_accepted(self):
        with self.assertRaises(ValueError):
            requirements.markdown_rubric("| 评分项 | 分值 |\n| --- | --- |\n| 功能 | 大部分 |")

    def test_markdown_never_silently_truncated(self):
        with self.assertRaises(ValueError):
            requirements.validate_requirements("x" * 60001)

    def test_explicit_parenthetical_point_allocations(self):
        rows = requirements.markdown_rubric("# 标准\n代码质量（20分）\n问题分析（30分）\n测试验证（50分）")
        self.assertEqual(sum(r["points"] for r in rows), 100)
        self.assertEqual(len(rows), 3)

    def test_no_fatal_defect_is_not_confused_with_missing_work(self):
        policy = requirements.grading_policy("正常工作量，无致命缺陷，尽量给92以上。\n缺的东西太多，给85。\n迟交成绩为80%\n评论40字以内\n异常人工审查")
        self.assertEqual(policy["normal_min_score"], 92)
        self.assertEqual(policy["insufficient_score"], 85)
        self.assertEqual(policy["late_multiplier"], .8)

    def test_late_adjustment_is_applied_once(self):
        result = {"score": 75, "assessment": "normal", "feedback": "可以"}
        policy = {"normal_min_score": 92, "late_multiplier": .8}
        output = requirements.apply_policy(result, policy, 100, late=True)
        self.assertEqual(output["raw_score"], 75)
        self.assertEqual(output["policy_score"], 92)
        self.assertEqual(output["final_score"], 73.6)

    def test_anomaly_has_no_automatic_final_grade(self):
        output = requirements.apply_policy({"score": 50, "assessment": "anomaly", "feedback": "缺少可读取内容"}, {"anomaly_manual_review": True}, 100)
        self.assertIsNone(output["final_score"])
        self.assertEqual(output["status"], "manual_review")


class ConfigurationTests(unittest.TestCase):
    def test_generic_model_config_without_private_api_doc(self):
        with patch.object(config, "settings", return_value={"models": [MODEL]}), patch.dict("os.environ", {}, clear=True):
            self.assertEqual(config.models_from_doc()[0]["name"], MODEL["name"])

    def test_remote_http_with_secret_rejected(self):
        model = {**MODEL, "base_url": "http://remote.example/v1"}
        with patch.object(config, "settings", return_value={"models": [model]}), patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                config.models_from_doc()

    def test_local_model_without_api_key_supported(self):
        model = {"name": "local-model", "base_url": "http://127.0.0.1:11434/v1"}
        with patch.object(config, "settings", return_value={"models": [model]}), patch.dict("os.environ", {}, clear=True):
            self.assertEqual(config.models_from_doc()[0]["api_key"], "")

    def test_v1_not_duplicated_in_model_endpoint(self):
        self.assertEqual(net.chat_completions_url("https://model.example/v1/"), "https://model.example/v1/chat/completions")
        self.assertEqual(net.chat_completions_url("https://model.example"), "https://model.example/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
