# 复旦 eLearning 助教 AI 作业批改指南

mini-homework-agent 用于 `https://elearning.fudan.edu.cn` 上的 Canvas 课程。它读取助教已有权限范围内的课程、作业和提交，由 AI 生成建议，人工审核后再回写。

## 配置凭证

在平台个人设置中创建访问令牌，按你自己的课程用途设置有效期和授权范围。令牌仍受账号在课程中的权限限制。运行 `mini-homework-agent init`，把平台地址设为 `https://elearning.fudan.edu.cn`，并在私有 `.secrets.json` 中填写 token。

该项目曾验证助教角色可以读取两门 Python 课的课程和作业列表。不同课程的权限设置可能不同；真实成绩回写须由助教审核并明确操作。遇到 401 不应直接推断为需要管理员权限。

## 在 iTerm 中只读检查

```bash
python3 scripts/test_canvas.py
# 或使用已保存的 token
python3 scripts/test_canvas.py --saved-token
# 仅检查课程列表
python3 scripts/test_canvas.py --saved-token --courses-only
# 指定自己的课程
python3 scripts/test_canvas.py --saved-token --course-id COURSE_ID
```

脚本使用本机 curl。手工输入令牌不回显，不把令牌放进进程参数。只发送 GET；课程列表失败时停止。输出包含课程/作业名称，请按需隐藏课程信息后分享，切勿再次分享 token。

## 作业题目在课件 PPT 上怎么办

把课件中的完整作业要求整理为 Markdown，粘贴或导入审核页面。包括题目、输入输出、边界情况、提交要求、AI 调试日志要求及评分要点。Agent 以你提供的 Markdown 为依据，避免从“根据课件要求完成”这一句话猜题目。

要按题目分别计分，可在 Markdown 中提供明确的评分表，列头使用“评分项 / 分值 / 要求”。总分与 Canvas 作业满分一致；若另行提供 JSON rubric，它会优先使用。

## 审核

默认先批 5 份未评分提交。重点核对题目理解、遗漏内容、扣分依据和评语。AI 不执行学生程序，也没有自动查重；不能把静态分析当作运行测试结果。

逐份确认后，再点击“回写已审核结果”。平台可能立即展示评语和分数。重新提交、题目变化或已有成绩变动会使本批更新被阻止；逾期规则可能由 Canvas 自动扣减分数。

## 数据范围

模型会收到你提供的要求和提交正文。姓名/学号等 Canvas 元数据不主动发送，但学生写在报告或代码中的个人信息仍可能存在。请选择适合课程数据的模型服务，并保存本地审核记录。

本项目由社区维护，与复旦大学没有官方授权或隶属关系。接口说明依据 [Canvas Assignments API](https://developerdocs.instructure.com/services/canvas/resources/assignments) 和 [Submissions API](https://developerdocs.instructure.com/services/canvas/resources/submissions)。
