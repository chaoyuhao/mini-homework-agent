#!/usr/bin/env python3
"""Read-only Canvas connectivity check, using the computer's curl client."""
import argparse
import getpass
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE = "https://elearning.fudan.edu.cn"
ROOT = Path(__file__).resolve().parent.parent
MARKER = "\n__CANVAS_HTTP_STATUS__"


def safe_text(value, token):
    text = str(value).replace(token, "[令牌已隐藏]")
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text)[:400]


def read_token(saved=False):
    if saved:
        path = ROOT / ".secrets.json"
        try:
            token = json.loads(path.read_text(encoding="utf-8")).get("canvas_token", "")
        except (OSError, ValueError):
            raise ValueError("无法读取项目的 .secrets.json") from None
        print("凭证来源：项目 .secrets.json（不会显示内容）")
    else:
        if not sys.stdin.isatty():
            raise ValueError("请直接在 iTerm 中运行，或使用 --saved-token")
        token = getpass.getpass("粘贴 Canvas token 后按回车（输入不显示）：")
    if not isinstance(token, str):
        raise ValueError("令牌必须是文本")
    token = token.strip()
    if not token or len(token) > 4096 or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
        raise ValueError("令牌为空或格式异常；请仅粘贴 token，不要包含 Bearer 前缀")
    return token


def request(path, token):
    # Passing the header on stdin keeps the token out of process arguments and shell history.
    curl_config = 'header = "Authorization: Bearer ' + token + '"\n'
    try:
        result = subprocess.run(
            ["curl", "--disable", "--config", "-", "--silent", "--show-error",
             "--request", "GET", "--connect-timeout", "10", "--max-time", "30",
             "--proto", "=https", "--header", "Accept: application/json",
             "--header", "Cache-Control: no-cache", "--write-out", MARKER + "%{http_code}",
             BASE + path],
            input=curl_config, capture_output=True, text=True, timeout=35,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("请求超时") from None
    if result.returncode:
        raise RuntimeError("curl 连接失败：" + safe_text(result.stderr, token))
    body, separator, status = result.stdout.rpartition(MARKER)
    if not separator or not status.strip().isdigit():
        raise RuntimeError("无法解析 curl 的 HTTP 状态码")
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    return int(status.strip()), data


def check(path, token):
    print("\nGET " + path, flush=True)
    status, data = request(path, token)
    print("HTTP " + str(status))
    if status != 200:
        if isinstance(data, dict):
            errors = data.get("errors", [])
            if isinstance(errors, list):
                for error in errors[:3]:
                    if isinstance(error, dict) and error.get("message"):
                        print("平台提示：" + safe_text(error["message"], token))
            elif isinstance(errors, dict):
                print("平台提示：" + safe_text(json.dumps(errors, ensure_ascii=False), token))
        if 300 <= status < 400:
            print("平台返回重定向；脚本没有跟随，也没有向其他地址转发令牌。")
        elif status == 401:
            print("此接口未接受凭证；仅凭 401 不能断言是助教权限不足。")
        elif status == 403:
            print("此接口拒绝访问；请检查账号的课程权限或令牌授权范围。")
        return None
    if not isinstance(data, list):
        print("接口返回格式异常，预期为 JSON 列表。")
        return None
    return data


def main():
    parser = argparse.ArgumentParser(description="只读检查复旦 Canvas API，不读取学生提交、不修改成绩")
    parser.add_argument("--saved-token", action="store_true", help="使用项目 .secrets.json，免去手工输入")
    parser.add_argument("--courses-only", action="store_true", help="仅检查课程列表，不检查作业列表")
    parser.add_argument("--course-id", action="append", type=int, help="指定要检查的课程，可重复；默认检查账号的 Python 助教课程")
    args = parser.parse_args()
    print("复旦 Canvas API · 本机只读测试")
    print("只发送 GET：当前账号课程，以及所选助教/教师课程的作业列表。")
    print("不会修改成绩、权限或令牌；不会保存输入的令牌。")
    if not shutil.which("curl"):
        print("错误：未找到 curl", file=sys.stderr)
        return 1
    try:
        token = read_token(args.saved_token)
        courses = check("/api/v1/courses?per_page=100", token)
        if courses is None:
            print("\n课程列表检查失败，已停止后续请求。")
            return 1
        print("当前页课程数：" + str(len(courses)))
        found = {}
        for course in courses:
            roles = [e.get("type", "unknown") for e in course.get("enrollments", [])]
            print(f"  {course.get('id')} | {safe_text(course.get('name', ''), token)} | 角色：{', '.join(roles)}")
            selected = course.get("id") in args.course_id if args.course_id else "python" in course.get("name", "").lower()
            if selected and any(role in ("ta", "teacher") for role in roles):
                found[course["id"]] = course
        if args.courses_only:
            print("\n课程列表访问成功。")
            return 0
        success = True
        for course_id in (args.course_id or sorted(found)):
            if course_id not in found:
                print(f"\n跳过课程 {course_id}：当前课程列表未显示该课的助教/教师身份。")
                continue
            rows = check(f"/api/v1/courses/{course_id}/assignments?per_page=100", token)
            if rows is None:
                success = False
                continue
            print("当前页作业数：" + str(len(rows)))
            for assignment in rows[:10]:
                print(f"  {assignment.get('id')} | {safe_text(assignment.get('name', ''), token)} | 满分：{assignment.get('points_possible')}")
            if len(rows) > 10:
                print("  ……仅显示前 10 个作业")
        print("\n测试结束。可将上述输出发给我继续定位；输出不包含令牌。")
        return 0 if success else 1
    except (ValueError, RuntimeError, OSError) as e:
        print("错误：" + str(e), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\n测试已取消。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
