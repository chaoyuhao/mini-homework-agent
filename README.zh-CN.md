# mini-homework-agent · 复旦 eLearning / Canvas AI 作业批改 Agent

一个简单的 Python 作业批改助手：**你用 Markdown 告诉 AI 作业要求，Agent 读取 Canvas/eLearning 提交，生成分数、依据和评语，你审核后再回写成绩。**

适用于复旦大学助教及其他使用 Canvas LMS 的教师、助教。核心循环来自 mini-swe-agent，模型接口支持 OpenAI 兼容服务。

[English](README.md) · [项目文档](https://github.com/chaoyuhao/mini-homework-agent/tree/main/docs) · [给你的 AI Agent 用](docs/agent-integration.md) · [复旦助教使用指南](docs/fudan-elearning.md)

![mini-homework-agent 本地审核页面：Markdown 作业要求、Python 代码、逐项评分和人工审核](docs/review-preview.jpg)

## 先体验，不用申请任何凭证

需要 Python 3.10+，支持 macOS、Linux 或 WSL。

```bash
git clone https://github.com/chaoyuhao/mini-homework-agent.git
cd mini-homework-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[pdf]'
mini-homework-agent demo --offline
mini-homework-agent serve
```

打开 **http://127.0.0.1:8765**，查看两份合成代码的审核流程。离线演示使用固定结果，不调用真实模型，也不能回写课程。

## 接入复旦 eLearning

```bash
mini-homework-agent init
```

在当前目录生成 `.secrets.json`，权限为 `0600`。填入自己的 Canvas token 和模型配置，格式见 [.secrets.example.json](.secrets.example.json)。复旦的平台地址为 `https://elearning.fudan.edu.cn`；其他学校改为自己的 Canvas 地址。

模型配置包含 `name`、`base_url`、`api_key`，可添加多个。环境变量 `CANVAS_TOKEN`、`CANVAS_BASE_URL`、`MODEL_NAME`、`MODEL_BASE_URL`、`MODEL_API_KEY` 也受支持。不要将真实 token 写进 README、截图或 issue。

重新启动审核页面，点击“连接课程”，选择课程和作业。页面只展示账号已有教师/助教身份的课程；程序不会给账号增加权限。

## 每次提供 Markdown 作业要求

可以直接粘贴，也可以在页面选择本地 `.md` 文件。题目、输入输出、边界情况、提交要求和评分要点都写在里面；这份 Markdown 会随本批记录保存。

```markdown
# 偶数求和
实现 sum_even(numbers)，累加所有偶数。支持负数；空列表返回 0。

| 评分项 | 分值 | 要求 |
| --- | --- | --- |
| 正确性 | 8 | 正确筛选偶数、支持空列表和负数，不修改输入 |
| 可读性 | 2 | 命名清晰、结构简洁 |
```

评分表总分应与平台满分一致。优先级是：自定义 JSON rubric → Markdown 评分表 → 平台 rubric → 明确标记的临时标准。没有明确标准时，临时标准按功能 60%、题目要求的边界情况 25%、可读性 15% 分配，需你确认是否适用。

完整例子：[examples/sum_even.md](examples/sum_even.md)。

## 批改、审核、回写

1. 选择作业，输入 Markdown，默认先批 5 份已提交且未评分的个人作业。
2. Agent 使用 mini-swe-agent 的轻量循环生成草稿。工具只能读取提交片段、做 AST 语法检查和校验评分，不能执行学生程序或回写成绩。
3. 查看原代码、逐项依据及评语，必要时修改最终分数。改分必须记录理由。
4. 逐份点击“确认这份评分”，只是保存审核。
5. 另行点击“回写已审核结果”并确认，才会更新平台分数和评语。学生可能马上看到结果，取决于课程发布策略。

```bash
# 用真实模型批改本地示例，不连接 Canvas
mini-homework-agent grade-local \
  --requirements examples/sum_even.md \
  --submission examples/sum_even.py --points 10

# 读取账号可访问的课程和作业
mini-homework-agent courses
mini-homework-agent assignments COURSE_ID

# 只生成草稿
mini-homework-agent grade COURSE_ID ASSIGNMENT_ID \
  --requirements 作业要求.md --limit 5

# 保留已完成结果，仅补判失败项
mini-homework-agent resume RUN_ID --workers 1 --request-interval 5
```

用 `--limit 0` 处理全部符合条件的提交；已评分提交默认跳过，只有显式 `--include-graded` 才生成重评草稿。`publish RUN_ID --confirm` 只回写已审核结果；`reconcile RUN_ID` 用于核对响应不确定的回写，避免重复发送评语。

默认逐份处理，平台请求至少间隔 5 秒；页面也可选择处理速度。遇到 Canvas 401/403 会暂停并保存已有结果，修复凭证或权限后点击“慢速重试失败项”，或运行 `resume`。续跑沿用保存的 Markdown、标准和模型，复用已下载原件，保留成功草稿、人工审核和确需人工评阅的异常。题目或提交版本变化时需要另建批次。

默认沿用电脑的系统/环境代理。如果已连接的 VPN 可以直达课程和模型服务，可在私有 `.secrets.json` 中设置 `"network_mode": "direct"`，让本项目的课程、附件、模型请求绕过 HTTP 代理。TLS 证书校验仍然开启，电脑的全局代理设置不变；直连不可用时使用默认的 `"system"`。

## 附件下载与图片读取

原附件先下载到私有 `.data/attachments/`，审核页可以重新下载原件。AI 读取提取后的正文、代码和 OCR 文字。macOS 安装 Swift 命令行工具后，可以本机识别截图、Word/PPT 图片和 PDF 页面；其他系统可调用已安装的 Tesseract（中文/英文语言包），扫描 PDF 可能需人工处理。OCR 标点和缩进可能有误，必须对照原件。

旧版 `.doc` 在 macOS 通过 `textutil` 转换，RAR 暂不支持，Pages 预览不等于完整文档。读不全的提交进入人工审查，不自动给零分。Markdown 里明确给出的宽松评分、迟交系数、评语字数及异常审查规则会独立记录；原始逐项分与政策建议分分别保存，避免隐含改分。

## 让其他 Agent 帮你判作业

可以让 Codex、Claude Code 或其他具备终端能力的 assistant 阅读本仓库，然后按 CLI 操作。建议先让它阅读 [docs/agent-integration.md](docs/agent-integration.md)，明确课程、作业、模型服务和你的 Markdown 要求。

适合搜索的使用场景包括：复旦 eLearning 自动判作业、Canvas AI homework grading、Python 作业批改、助教评分 agent、Markdown rubric。项目通过真实流程和文档覆盖这些场景，不保证搜索排名。

## 当前能力与限制

支持 `.py`、Notebook 代码/Markdown 单元、文本、ZIP、DOCX、PPTX 和 PDF，以及可 OCR 的图片。不提供代码运行测试、查重或抄袭判断；截图和文档图片可在本机 OCR，识别结果会提示复核；小组作业、测验、非数值评分及零分作业暂不支持。

AI 可能误判，静态评阅不能证明程序正确。缺失、读取失败、截断、低置信度都提示复核，不会当作零分。模型收到作业要求和提交正文；程序不主动发送 Canvas token、学生姓名/学号元数据，但正文里写了个人信息仍可能一起发送。

草稿和轨迹存在 `.data/`，凭证存在 `.secrets.json`，都被 Git 排除。导出的 JSON 包含课程数据，请妥善保存。发布前会重查提交和成绩；避免与其他助教同时修改同一批作业。逾期扣分由 Canvas 的课程策略决定。

这是社区项目，并非复旦大学或 Instructure 官方产品。详细配置与边界见 [英文 README](README.md) 和 [安全说明](SECURITY.md)。

## 参与开源

欢迎提供合成作业例子、Canvas 兼容修复、Markdown 标准格式、文档和界面改进。使用真实学生数据前请先移除个人信息；公开 issue 不要附 token、学生原作业或带签名的附件链接。

[贡献指南](CONTRIBUTING.md) · [提交问题](https://github.com/chaoyuhao/mini-homework-agent/issues/new/choose)

如果它帮你省了批改时间，可以给仓库一个 Star，让更多助教找到它。

MIT 开源；mini-swe-agent 的上游版本与许可证见 [UPSTREAM.md](grader/_vendor/mini_swe_agent/UPSTREAM.md)。
