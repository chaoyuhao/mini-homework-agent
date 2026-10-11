import re

MAX_REQUIREMENTS = 60_000


def validate_requirements(markdown):
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("请先提供本次作业要求（粘贴 Markdown 或选择 .md 文件）")
    if len(markdown) > MAX_REQUIREMENTS:
        raise ValueError("作业要求超过 60000 字符，请精简后重试；不会自动截断评分依据")
    return markdown.strip()


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
    if "异常" in markdown and re.search(r"人工(?:审查|审核|复核)", markdown):
        result["anomaly_manual_review"] = True
    return result


def apply_policy(result, policy, maximum, *, late=False):
    base = result["score"]
    assessment = result.get("assessment", "normal")
    if assessment == "anomaly" and policy.get("anomaly_manual_review"):
        return {"raw_score": base, "policy_score": None, "final_score": None, "status": "manual_review", "needs_review": True, "final_feedback": result["feedback"]}
    if assessment == "normal" and "normal_min_score" in policy:
        base = max(base, policy["normal_min_score"])
    elif assessment == "insufficient" and "insufficient_score" in policy:
        base = policy["insufficient_score"]
    if not 0 <= base <= maximum:
        raise ValueError("Markdown 宽松评分规则超出作业满分")
    final = round(base * policy.get("late_multiplier", 1), 2) if late else round(base, 2)
    return {"raw_score": result["score"], "policy_score": round(base, 2), "late_multiplier_applied": policy.get("late_multiplier", 1) if late else 1, "final_score": final, "final_feedback": result["feedback"]}
