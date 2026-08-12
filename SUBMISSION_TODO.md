# GlassBox Agent 笔试提交 TODO

这份清单是本项目后续工作的唯一进度入口。每完成一项，就勾选对应验收条件，再进入下一项。

状态说明：

- `[x]` 已完成并有证据。
- `[ ]` 尚未完成。
- `▶ CURRENT` 当前只处理这一项。
- `USER` 需要你本人操作，例如填写密钥、创建 GitHub 仓库或上传录屏。
- `CODEX` 可以由 Codex 完成并验证。

## 当前快照

- [x] 核心 Agent Runtime、4 个工具、Session、Context 压缩、Replay、Fork 已实现。
- [x] README、AI 辅助开发记录、CI、JSONL Trace 示例已创建。
- [x] 本地 Ruff 与自动化测试通过：39 tests，分支覆盖率 96.27%。
- [x] 本地 `.env` 已配置，且被 Git 忽略。
- [x] 真实 `doctor` 与 3 个 live eval 已通过。
- [x] 五个模块的架构设计答案已整理为仓库内 `ARCHITECTURE_DESIGN.md`。
- [ ] Git 仓库尚无 remote，也尚未提交初始 commit。
- [x] 演示视频已录制并完成画面、流程和密钥检查；尚待上传 GitHub Release。

## 1. 配置真实 DeepSeek API（USER）

目标：在本机安全配置 API key，不把密钥发到聊天、Git 或录屏中。

- [x] 在项目根目录复制环境变量模板：

  ```powershell
  Copy-Item .env.example .env
  ```

- [x] 用本地编辑器打开 `.env`，填写 `DEEPSEEK_API_KEY`。
- [x] 确认 `.env` 仍被 `.gitignore` 忽略：

  ```powershell
  git check-ignore .env
  ```

- [x] 不在聊天中粘贴 key，不截取包含 key 的屏幕。

验收条件：`.env` 存在、key 非空、`git status` 不显示 `.env`。

完成后告诉 Codex：“第 1 项已配置”。Codex 将继续执行第 2 项，不需要你提供 key 内容。

## 2. 真实 API 兼容性与 Live Eval（CODEX）

目标：证明项目不是只依赖 Fake Provider，真实 DeepSeek 可以直答、调用工具并从错误中恢复。

- [x] 运行 `glassbox doctor`，验证模型名、tool calling、参数 JSON 和 `tool_call_id`。
- [x] 运行 `glassbox eval`。
- [x] Eval 1：无工具直接返回 `PONG`。
- [x] Eval 2：完成 `search_docs → read_doc → calculator → todo`，得到 360 元。
- [x] Eval 3：`search_docs` 首次受控失败后恢复，返回完整周报事实。
- [x] 检查事件和终端输出中不存在 API key。
- [x] 修复 live eval 暴露的 Windows SQLite 文件锁，并重新验证。

验收条件：`Doctor passed`，`Live eval: 3/3 passed`。

## 3. Live 验证后的代码收口（CODEX）

目标：把真实调用暴露的问题修完，并恢复全部质量门槛。

- [x] 运行 Ruff lint。
- [x] 运行 Ruff format check。
- [x] 运行全部 pytest 与分支覆盖率检查。
- [x] 覆盖率不低于 90%。
- [x] 检查 Windows Quickstart 命令可以复制运行。
- [x] 更新 README 中的真实验证结果。
- [x] 更新 AI 问题解决记录，写入真实 API 调试过程。

验收条件：lint/format 通过、全部测试通过、覆盖率 ≥90%。

## 4. 完成五个架构设计模块（USER + CODEX） ▶ CURRENT

目标：创建 `ARCHITECTURE_DESIGN.md`，每个模块选择并回答一道题。

建议选题：

- [x] 模块一，第 2 题：200 轮 Session 的 Context 压缩（已写入原笔试题 Markdown，最终提交前统一整理至 `ARCHITECTURE_DESIGN.md`）。
- [x] 模块二，第 1 题：半个月后的 Memory 召回（已写入原笔试题 Markdown，最终提交前统一整理至 `ARCHITECTURE_DESIGN.md`）。
- [x] 模块三，第 1 题：长程任务忘记目标（已写入原笔试题 Markdown）。
- [x] 模块四，第 2 题：Busy Session 收到新消息或异步事件（已写入原笔试题 Markdown）。
- [x] 模块五，第 1 题：Claude 与 OpenAI-compatible Function Calling 对比（已写入原笔试题 Markdown）。

每道答案必须包含：

- [ ] 问题拆解和设计目标。
- [ ] 架构或时序图。
- [ ] 核心数据结构与处理流程。
- [ ] 异常、降级和一致性策略。
- [ ] 方案优缺点及替代方案。
- [ ] 与 GlassBox 实现的联系，以及当前 MVP 没有覆盖的部分。
- [ ] 使用 AI 的思考过程、采纳与拒绝理由，而不是只给结论。

验收条件：五个模块各一题，内容能由你本人复述并回答追问。

## 5. 准备录屏脚本与干净演示数据（CODEX）

目标：录屏前先完整彩排，避免现场暴露密钥或遇到不可控模型行为。

- [x] 编写有声版 `RECORDING_SCRIPT.md` 与无旁白版 `SILENT_RECORDING_SCRIPT.md`；无旁白版配套 `silent-demo-cards.html` 全屏标题卡。
- [x] `start-recording.ps1` 每次创建一个全新的时间戳演示数据库。
- [x] 固定直答、多工具链、受控错误、Session 隔离、压缩、Replay、Fork 的提示词。
- [x] 彩排确认 Fork 必须选择最后一个 `assistant_message`；彩排 EVENT_ID 为 35，正式录制时按 trace 动态选择。
- [x] 使用真实 DeepSeek 完整彩排；多工具、压缩、Session 隔离和 Fork 断言全部通过，目标时长 6～8 分钟。
- [ ] 确认终端、编辑器、浏览器均不显示 API key 或无关个人信息。

验收条件：不剪辑也能在 8 分钟内稳定走完整条演示路径。

## 6. 创建并发布 GitHub 仓库（USER + CODEX）

目标：生成面试官可以访问和复现的代码链接。

- [ ] 在 GitHub 创建 `glassbox-agent` 仓库。
- [ ] 配置 `origin` remote。
- [ ] 运行 secret scan，确认仓库、日志和示例中没有 key。
- [ ] 创建清晰的初始 commit。
- [ ] 推送 `main` 分支。
- [ ] 从 GitHub 页面检查 README、Mermaid 图和文件链接是否正常。
- [ ] 选择公开仓库，或确认私有仓库已授权面试官访问。

验收条件：从另一台机器或无本地状态环境可以打开仓库并按照 README 安装。

## 7. 正式录屏并上传（USER）

目标：用真实 API 展示设计差异，而不是只口头介绍代码。

- [x] 录制 `doctor`。
- [x] 展示直答和多工具链。
- [x] 展示 `/trace` 的参数、结果、错误、重试与耗时。
- [x] 展示两个 Session 的消息和 Todo 隔离。
- [x] 展示 Context 压缩。
- [x] 展示 Offline Replay（最终发布前确认断网操作在视频中表达清楚）。
- [x] 从完成 turn 执行 Fork，展示父子独立演进。
- [x] 简短展示测试与覆盖率。
- [ ] 剪掉开头约 40 秒的上一轮 Replay/测试预录内容；可选增加 3 秒标题页。
- [x] 回看视频，确认关键终端文字可读、流程完整、无密钥。
- [ ] 上传到面试官无需额外申请权限即可访问的位置。

验收条件：获得可访问的视频链接。

## 8. 最终提交审计（USER + CODEX）

目标：形成一个可以直接发给面试官的提交包。

- [ ] README 顶部补充 GitHub、录屏和架构设计链接。
- [x] `AI_ASSISTED_DEVELOPMENT.md` 包含真实问题和取舍。
- [x] `ARCHITECTURE_DESIGN.md` 已进入仓库。
- [x] `.env.example` 只有变量名和安全默认值。
- [x] 再次执行 secret scan，只有 `.env.example` 和 README 中的占位值。
- [x] 再次执行 lint、测试和覆盖率：39 tests，96.27%。
- [ ] 在临时目录重新 clone，按 README 完成首次真实请求。
- [ ] 检查所有链接的访问权限。
- [ ] 准备最终提交消息。

验收条件：代码链接、录屏链接、README、AI 记录和五模块答案全部可访问。

## 最终提交消息模板

```text
您好，以下是我的 Agent 技术笔试提交：

1. Vibe Coding 代码仓库
GitHub：<链接>

2. 操作录屏
Video：<链接>

3. 架构设计题
见仓库 ARCHITECTURE_DESIGN.md：<链接>

4. 运行与设计说明
见仓库 README.md

5. AI Prompt 与问题解决记录
见仓库 AI_ASSISTED_DEVELOPMENT.md
```
