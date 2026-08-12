# AI Assisted Development Record

本文记录 GlassBox Agent 开发中使用 AI 的方式。AI 用于需求拆解、设计对照、代码生成建议、测试
补全与文档审阅；最终架构边界、取舍和验收标准由项目目标约束，并用可执行测试验证。

## 1. 初始问题与提示

核心输入可以概括为：

> 从零实现一个最小可用终端 Agent，真实调用 DeepSeek，不依赖 Agent 框架。需要 tool loop、至少
> 3 个工具、session、context 压缩、错误处理、日志和测试。希望借鉴 Claude Code/Codex，又有
> 自己的差异化。

随后将目标收敛为：

> 用 append-only 事件账本和 pure reducer 实现“Agent 飞行记录仪”；所有运行、恢复、context、
> replay 与 fork 走同一状态重建路径；不展示或保存原始 CoT。

实现阶段使用过的代表性追问：

1. “如何在不使用 LangGraph 的前提下实现 OpenAI-compatible 多轮 tool loop？”
2. “如何让 todo 的状态可以被 replay 和 fork 真实恢复，而不是放进独立业务表？”
3. “怎样只压缩完整 turn，保证 tool call/result 不被拆开？”
4. “DeepSeek 的 timeout、429、5xx 与 4xx 应如何分类重试？”
5. “如何证明 replay 完全离线、两 session 无串扰、API key 和 reasoning 不落盘？”
6. “当前 DeepSeek V4 的模型名、tool call 协议和 thinking 默认值是什么？”

## 2. 采纳的建议

| 建议 | 采纳理由 | 落地方式 |
|---|---|---|
| 事件溯源作为差异化 | replay、fork、审计和 session 恢复可以共享事实模型 | SQLite events + `reduce_events` |
| UI 与 runtime 解耦 | CLI 只是适配器，核心更容易测试 | `cli.py` 不持有运行状态 |
| 工具使用 Pydantic schema | schema、校验与错误格式来自同一类型 | `ToolSpec.args_model` |
| Todo 由事件投影 | 避免 replay 只恢复消息、恢复不了业务状态 | 成功结果携带 `todo_mutation` |
| 只压缩完整 turn | 保持 provider 消息协议合法 | checkpoint 只能落在完成边界 |
| 重试是 runtime 事件 | 录屏可以解释故障与恢复，而不是只看到最终结果 | `retry_scheduled` |
| 用事实约束 live eval | 模型措辞不稳定，不应逐字匹配 | 工具集合、关键事实、todo、pending errors |
| 非思考模式显式开关 | V4 默认 thinking；工具循环需额外回传 reasoning | `thinking.type=disabled` |

## 3. 拒绝或调整的建议

| 建议 | 决定 | 原因 |
|---|---|---|
| 直接采用 LangGraph | 拒绝 | 违反核心 Agent Runtime 自行实现的题面要求 |
| Todo 使用独立数据库表 | 拒绝 | 会形成第二事实源，fork/replay 易出现状态漂移 |
| Replay 时重新执行工具 | 拒绝 | 副作用和外部数据不可确定，可能重复操作 |
| 将 reasoning_content 加密后保存 | 拒绝 | 加密仍是持久化，不符合产品和隐私边界 |
| 默认启用 DeepSeek strict beta | 暂不采用 | 需要 beta endpoint 且支持的 JSON Schema 是子集，MVP 先做本地强校验 |
| 上向量数据库做 memory | 拒绝 | 超出一天 MVP；当前 session capsule 足以展示放置与召回策略 |
| 并发执行多个工具 | 暂不采用 | 题面不要求；顺序执行让 trace、副作用和 reducer 语义更清楚 |
| 声称 fork 是确定性重演 | 拒绝 | 只能承诺历史事件的确定性投影，不能承诺模型/工具重跑一致 |

## 4. 真实问题与修复记录

### Windows TOML 反斜杠

- 现象：`pip install -e ".[dev]"` 解析 `pyproject.toml` 失败。
- 原因：coverage 排除表达式在 TOML 双引号字符串中包含未转义反斜杠。
- 修复：改用 TOML literal string。
- 验证：Windows Python 3.12 editable install 成功。

### 被 SDK 包装的 timeout 没有重试

- 现象：provider 测试中 `RuntimeError("timeout")` 被当作不可重试错误。
- 原因：分类器只检查异常类名，没有检查消息和 HTTP 状态。
- 修复：同时检查 `status_code` 与“类型 + 消息”指纹；429/5xx、timeout、connection 可重试。
- 验证：测试覆盖 timeout、429 与 401 不重试。

### DeepSeek V4 默认 thinking 与设计承诺不一致

- 现象：初始 provider 只设置 temperature，没有显式关闭 thinking。
- 原因：旧模型习惯与当前 V4 官方默认值不同；V4 默认开启 thinking，工具循环还要求回传完整
  `reasoning_content`。
- 修复：所有聊天、摘要与 doctor 请求显式发送 `thinking.type=disabled`；runtime 不写任何
  reasoning 字段或派生信息。
- 验证：provider 请求参数测试，以及 JSONL 中不存在私有 reasoning 文本/字段的安全测试。

### 工具超时的线程边界

- 现象：`ThreadPoolExecutor` context manager 在 timeout 后仍等待 worker，表面超时但调用不返回。
- 原因：退出 context manager 默认 `shutdown(wait=True)`。
- 修复：显式 `shutdown(wait=False, cancel_futures=True)`。
- 限制：Python 线程无法强杀已运行 handler；README 明确生产环境应使用进程隔离。

### SQLite 上下文退出后仍锁住 Windows 临时文件

- 现象：三个真实 DeepSeek eval 都显示 PASS，但命令退出时删除临时 `eval.db` 触发 WinError 32。
- 最小复现：只创建 `EventStore`、新建一个 session 后立即删除数据库，也能稳定复现文件锁。
- 原因：`with sqlite3.Connection` 只负责事务提交或回滚，并不会关闭连接；Windows 不允许删除仍被
  打开的数据库文件。
- 修复：把 `EventStore._connect()` 改为显式管理 commit、rollback 和 `close()` 的上下文管理器。
- 回归测试：每次存储操作返回后立即删除数据库文件；旧实现会在 Windows 失败。
- 验证：39 个自动化测试通过，随后真实 `glassbox eval` 完整退出并得到 `3/3 passed`。

### Trace 信息不足

- 现象：早期 trace 只显示 event type，故障演示难以讲清恢复过程。
- 修复：增加工具参数、ok/error code、耗时、LLM step、usage、retry number 与 context 字符估算。

## 5. AI 输出如何被验证

AI 建议不直接作为“正确性证明”，所有关键承诺都映射到自动化测试或可观察命令：

- Agent loop：Fake provider 驱动直答、多工具链、失败恢复和 max steps。
- 状态一致性：`outcome.state == replay(session)`，replay 前后 provider 调用数不变。
- 隔离：两个 session 分别创建 todo，互相看不到消息和 todo。
- Fork：只接受完成事件，继承前缀，父子后续事件独立。
- 压缩：成功 capsule、失败滑窗、完整 turn 边界。
- 安全：AST calculator 拒绝调用/属性访问；API key 和 reasoning 不出现在 JSONL。
- 真实 API：`glassbox doctor` 通过，三个 DeepSeek live eval 全部通过。
- 质量：Ruff 通过，39 个测试通过，分支覆盖率 96.27%。

## 6. 提交验证

- 代码、测试、架构答案和开发记录统一进入 GitHub 仓库。
- 演示使用独立时间戳数据库，未暴露 API Key；视频作为 GitHub Release 附件发布。
- README 顶部集中提供全部提交材料入口，面试官无需在目录中逐项寻找。
