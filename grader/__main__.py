import argparse
import json
import sys

from .canvas import Canvas
from .config import DATA, save_json, models_from_doc
from .llm import benchmark
from . import workflow


def main():
    parser = argparse.ArgumentParser(description="mini-homework-agent · Markdown 作业要求 + Canvas 批改与审核")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="在当前工作目录创建仅本人可读的配置模板")
    commands.add_parser("courses", help="列出当前账号课程")
    assignments = commands.add_parser("assignments", help="列出某门课的作业")
    assignments.add_argument("course_id", type=int)
    bench = commands.add_parser("benchmark", help="实测模型延迟并选择最快的合格模型")
    bench.add_argument("--rounds", type=int, default=2)
    demo = commands.add_parser("demo", help="批改两个合成样例，不连接 Canvas")
    demo.add_argument("--offline", action="store_true", help="固定示例输出，无需模型或 Canvas 凭证")
    web = commands.add_parser("serve", help="启动本地审核页面")
    web.add_argument("--port", type=int, default=8765)
    batch = commands.add_parser("grade", help="批改作业并保存草稿，不回写平台")
    batch.add_argument("course_id", type=int)
    batch.add_argument("assignment_id", type=int)
    batch.add_argument("--limit", type=int, default=5, help="默认先批 5 份；0 为全部")
    batch.add_argument("--workers", type=int, default=1, help="并行数量，默认逐份处理")
    batch.add_argument("--request-interval", type=float, default=5, help="Canvas 请求间隔（秒），默认 5")
    batch.add_argument("--model")
    batch.add_argument("--rubric", help="自定义 rubric JSON 文件")
    batch.add_argument("--requirements", required=True, help="本次作业要求的 Markdown 文件")
    batch.add_argument("--notes", default="")
    batch.add_argument("--include-graded", action="store_true")
    resume = commands.add_parser("resume", help="慢速续跑失败项，保留已有草稿和人工审核")
    resume.add_argument("run_id")
    resume.add_argument("--workers", type=int, default=1)
    resume.add_argument("--request-interval", type=float, default=5)
    resume.add_argument("--model", help="仅为失败项改用另一模型；已有结果保留")
    local = commands.add_parser("grade-local", help="按 Markdown 要求批改本地提交，不连接 Canvas")
    local.add_argument("--requirements", required=True)
    local.add_argument("--submission", required=True)
    local.add_argument("--points", type=float, default=100)
    local.add_argument("--title", default="本地作业")
    local.add_argument("--model")
    local.add_argument("--rubric")
    publish = commands.add_parser("publish", help="只回写已在页面审核通过的结果")
    publish.add_argument("run_id")
    publish.add_argument("--confirm", action="store_true", required=True)
    reconcile = commands.add_parser("reconcile", help="核对回写中断的批次，避免重复评语")
    reconcile.add_argument("run_id")
    model = commands.add_parser("model", help="切换默认模型")
    model.add_argument("name")
    args = parser.parse_args()
    def progress(run):
        completed = run.get("progress_completed", len(run["results"]))
        total = run.get("progress_total", run["total"])
        print(f"批次 {run['id']}：本次 {completed}/{total}", flush=True)
    try:
        if args.command == "serve":
            from .web import serve
            return serve(args.port)
        if args.command == "init":
            from .config import initialize
            result = {"configuration": str(initialize()), "next": "填写 Canvas 令牌和模型服务配置，然后运行 serve"}
        elif args.command == "courses":
            result = [{"id": c["id"], "name": c.get("name"), "roles": [e["type"] for e in c.get("enrollments", [])]} for c in Canvas().courses()]
        elif args.command == "assignments":
            result = [{k: a.get(k) for k in ("id", "name", "points_possible", "due_at")} for a in Canvas().assignments(args.course_id)]
        elif args.command == "benchmark":
            if not 2 <= args.rounds <= 20:
                raise ValueError("测速轮数应在 2–20 之间")
            result = benchmark(args.rounds, progress=lambda r: print(json.dumps(r, ensure_ascii=False), flush=True))
        elif args.command == "model":
            if args.name not in [m["name"] for m in models_from_doc()]:
                raise ValueError("未知模型名称")
            save_json(DATA / "model.json", {"name": args.name})
            result = {"selected": args.name}
        elif args.command == "demo":
            result = workflow.demo(offline=args.offline)
        elif args.command == "grade":
            from pathlib import Path
            if args.limit < 0:
                raise ValueError("limit 不能为负数")
            rubric = json.loads(Path(args.rubric).read_text()) if args.rubric else None
            requirements = Path(args.requirements).read_text(encoding="utf-8-sig")
            result = workflow.batch(args.course_id, args.assignment_id, requirements=requirements, limit=args.limit, workers=args.workers, request_interval=args.request_interval, model_name=args.model, custom_rubric=rubric, notes=args.notes, include_graded=args.include_graded, progress=progress)
        elif args.command == "resume":
            result = workflow.resume(args.run_id, workers=args.workers, request_interval=args.request_interval, model_name=args.model, progress=progress)
        elif args.command == "grade-local":
            from pathlib import Path
            rubric = json.loads(Path(args.rubric).read_text()) if args.rubric else None
            result = workflow.grade_local(Path(args.requirements), Path(args.submission), title=args.title, points=args.points, model_name=args.model, custom_rubric=rubric)
        elif args.command == "publish":
            result = workflow.publish(args.run_id)
        else:
            result = workflow.reconcile(args.run_id)
        if isinstance(result, dict) and "results" in result and "id" in result:
            print(json.dumps({"run_id": result["id"], "status": result["status"], "count": len(result["results"]), "path": str(workflow.run_path(result["id"]))}, ensure_ascii=False, indent=2))
            if result.get("blocked_reason"):
                print(result["blocked_reason"], file=sys.stderr)
            if result["status"] == "paused":
                return 1
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError, OSError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
