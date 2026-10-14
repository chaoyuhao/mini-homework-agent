import re
import math

MAX_REQUIREMENTS = 60_000


def validate_requirements(markdown):
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("请先提供本次作业要求（粘贴 Markdown 或选择 .md 文件）")
    if len(markdown) > MAX_REQUIREMENTS:
        raise ValueError("作业要求超过 60000 字符，请精简后重试；不会自动截断评分依据")
    return markdown.strip()


def markdown_questions(markdown):
    """Read explicitly numbered question headings; never infer point weights."""
    lines = markdown.splitlines()
    visible = []
    fence = None
    for line in lines:
        marker = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            visible.append("")
        elif marker:
            fence = marker[1]
            visible.append("")
        else:
            visible.append(line)
    headings = []
    pattern = re.compile(r"^(#{1,6})\s+((?:第\s*(\d+)\s*题|(?:Question|Problem)\s+(\d+)\b).*)$", re.I)
    for index, line in enumerate(visible):
        match = pattern.match(line.strip())
        if match:
            headings.append((index, len(match[1]), match[2].strip(), int(match[3] or match[4])))
    if len(headings) > 30 or len({h[3] for h in headings}) != len(headings):
        raise ValueError("题号不能重复，且每次作业最多支持 30 道题")
    questions = []
    for start, depth, title, number in headings:
        end = start + 1
        while end < len(lines):
            heading = re.match(r"^(#{1,6})\s+", visible[end].strip())
            if heading and len(heading[1]) <= depth:
                break
            end += 1
        body = "\n".join(lines[start + 1:end]).strip()
        if not body:
            raise ValueError(f"第 {number} 题缺少作业要求")
        questions.append({"id": f"q_{number}", "title": title, "requirements": body})
    return questions


def holistic_scoring(markdown):
    return bool(re.search(r"^(?:#{1,6}\s+|[-*+]\s+)?(?:评分方式|scoring mode)\s*[:：]\s*(?:整体评分|holistic)\s*[。.]?\s*$", markdown, re.M | re.I))


def instruction_bundle(markdown, grading_rules="", *, maximum=None):
    """Snapshot task details and shared rules independently; task rules override."""
    markdown = validate_requirements(markdown)
    if not isinstance(grading_rules, str):
        raise ValueError("通用给分规则必须是 Markdown 文本，可以为空")
    grading_rules = grading_rules.strip()
    if len(markdown) + len(grading_rules) > MAX_REQUIREMENTS:
        raise ValueError("作业要求与通用规则合计超过 60000 字符，请精简后重试")
    policy = {**grading_policy(grading_rules), **grading_policy(markdown)}
    if maximum is not None:
        scores = [policy[k] for k in ("normal_min_score", "insufficient_score") if k in policy]
        scores += policy.get("student_feedback_scores", []) + policy.get("internal_reason_required_scores", [])
        if any(not math.isfinite(s) or not 0 <= s <= maximum for s in scores):
            raise ValueError("给分规则中的分数超出本次作业满分；请使用适用的通用规则")
    return {"instructor_markdown": markdown, "instructor_policy_markdown": grading_rules, "grading_policy": policy, "questions": markdown_questions(markdown)}


def markdown_rubric(markdown):
    """Read an explicit Markdown points table; never infer unstated weights."""
    lines = markdown.splitlines()
    headings = {"评分项", "评分标准", "项目", "维度", "题目", "criterion"}
    point_headings = {"分值", "满分", "分数", "points", "score"}
    description_headings = {"要求", "说明", "描述", "description"}

    def cells(line):
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    for i, line in enumerate(lines[:-1]):
        if "|" not in line:
            continue
        header = cells(line)
        normalized = [re.sub(r"[*` ]", "", h).lower() for h in header]
        title_idx = next((j for j, h in enumerate(normalized) if h in headings), None)
        points_idx = next((j for j, h in enumerate(normalized) if h in point_headings), None)
        if title_idx is None or points_idx is None:
            continue
        separator = cells(lines[i + 1])
        if len(separator) != len(header) or not all(re.fullmatch(r":?-{3,}:?", s) for s in separator):
            continue
        description_idx = next((j for j, h in enumerate(normalized) if h in description_headings), None)
        rows = []
        for row_line in lines[i + 2:]:
            if "|" not in row_line or not row_line.strip():
                break
            row = cells(row_line)
            if len(row) != len(header):
                raise ValueError("Markdown 评分表列数不一致")
            if row[title_idx].strip("*` ") in ("合计", "总分", "总计", "Total"):
                continue
            score = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*分?", row[points_idx])
            if not score:
                raise ValueError("Markdown 评分表的分值应为数字，例如 20 或 20分")
            description = row[title_idx]
            if description_idx is not None and row[description_idx]:
                description += "：" + row[description_idx]
            rows.append({"id": f"md_{len(rows) + 1}", "description": description, "points": float(score.group(1))})
        if rows:
            return rows
    # Also accept explicitly stated point allocations such as “代码质量（20分）”.
    rows = []
    for line in lines:
        match = re.fullmatch(r"\s*(?:[-*+]\s+|\d+[.)、]\s*)?([^#\n]+?)\s*[（(]\s*([0-9]+(?:\.[0-9]+)?)\s*分\s*[）)]\s*", line)
        if match:
            rows.append({"id": f"md_{len(rows) + 1}", "description": match.group(1).strip(), "points": float(match.group(2))})
    if rows:
        return rows
    return None


def grading_policy(markdown):
    """Recognize explicit leniency/late rules without inferring student intent."""
    result = {}
    late = re.search(r"迟交[^\n]{0,25}?(\d+(?:\.\d+)?)\s*[%％]", markdown)
    floor = re.search(r"(?:尽量|正常)[^\n]{0,25}?给\s*(\d+(?:\.\d+)?)\s*(?:分)?以上", markdown)
    missing = re.search(r"(?:缺的[^\n]{0,25}|缺失[^\n]{0,25}|缺少[^\n]{0,25})[^\n]{0,25}?给\s*(\d+(?:\.\d+)?)", markdown)
    feedback = re.search(r"(\d+)\s*字以内", markdown)
    feedback_score = re.search(r"只有\s*(\d+(?:\.\d+)?)\s*分[^\n。]{0,30}才有评语", markdown)
    internal_score = re.search(r"(\d+(?:\.\d+)?)\s*分[^\n。\d]{0,20}内部[^\n。\d]{0,12}(?:原因|依据)", markdown)
    if late:
        multiplier = float(late.group(1)) / 100
        if not 0 < multiplier <= 1:
            raise ValueError("迟交百分比必须在 0% 至 100% 之间")
        result["late_multiplier"] = multiplier
    if floor:
        result["normal_min_score"] = float(floor.group(1))
    if missing:
        result["insufficient_score"] = float(missing.group(1))
    if feedback:
        result["feedback_max_chars"] = min(int(feedback.group(1)), 5000)
    if feedback_score:
        result["student_feedback_scores"] = [float(feedback_score.group(1))]
    if internal_score:
        result["internal_reason_required_scores"] = [float(internal_score.group(1))]
    if "异常" in markdown and re.search(r"人工(?:审查|审核|复核)", markdown):
        result["anomaly_manual_review"] = True
    return result


def internal_reason(value):
    """Preserve local reasoning independently of the optional student comment."""
    explicit = value.get("internal_reason", "")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    deductions = [c["feedback"].strip() for c in value.get("criteria", []) if c.get("score", 0) < c.get("points", 0) and isinstance(c.get("feedback"), str) and c["feedback"].strip()]
    deductions += [q["feedback"].strip() for q in value.get("question_checks", []) if q.get("status") != "met" and isinstance(q.get("feedback"), str) and q["feedback"].strip()]
    if deductions:
        return "；".join(deductions)[:10000]
    return next((value[k].strip() for k in ("assessment_reason", "feedback") if isinstance(value.get(k), str) and value[k].strip()), "")


def score_in(score, scores):
    return isinstance(score, (int, float)) and not isinstance(score, bool) and any(math.isclose(score, s, rel_tol=0, abs_tol=1e-8) for s in scores)


def student_feedback(feedback, content_score, policy):
    if not isinstance(feedback, str):
        raise ValueError("评语必须是文本，可以为空")
    if "student_feedback_scores" in policy and not score_in(content_score, policy["student_feedback_scores"]):
        return ""
    return feedback.strip()


def apply_policy(result, policy, maximum, *, late=False):
    base = result["score"]
    reason = internal_reason(result)
    assessment = result.get("assessment", "normal")
    uncertain_question = any(q.get("status") == "uncertain" for q in result.get("question_checks", []))
    if uncertain_question or (assessment == "anomaly" and policy.get("anomaly_manual_review")):
        result = {"raw_score": base, "policy_score": None, "final_score": None, "status": "manual_review", "needs_review": True, "final_feedback": student_feedback(result.get("feedback", ""), None, policy), "internal_reason": reason}
        if uncertain_question:
            result["manual_review_reason"] = "某道题依据不足，无法确认完成情况，请对照原件人工评分"
        return result
    if assessment == "normal" and "normal_min_score" in policy:
        base = max(base, policy["normal_min_score"])
    elif assessment == "insufficient" and "insufficient_score" in policy:
        base = policy["insufficient_score"]
    if not 0 <= base <= maximum:
        raise ValueError("Markdown 宽松评分规则超出作业满分")
    final = round(base * policy.get("late_multiplier", 1), 2) if late else round(base, 2)
    return {"raw_score": result["score"], "policy_score": round(base, 2), "late_multiplier_applied": policy.get("late_multiplier", 1) if late else 1, "final_score": final, "final_feedback": student_feedback(result.get("feedback", ""), base, policy), "internal_reason": reason}
