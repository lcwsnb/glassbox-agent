# 演示视频

演示视频展示了以下可验收流程：

1. `glassbox doctor` 验证真实 DeepSeek API、工具调用协议和 `tool_call_id`。
2. 直接回答，以及 `search_docs → read_doc → calculator → todo` 多工具链。
3. 一次受控 `search_docs` 故障、结构化错误回灌和模型恢复，并通过 `/trace` 查看全过程。
4. Context 压缩事件和 `/context` 中的 Memory Capsule。
5. 两个 Session 的消息与 Todo 隔离。
6. 从完成 turn 创建 Fork，验证祖先状态继承和父子独立演进。
7. 断开模型调用后的离线 Replay，以及 Ruff、pytest 和分支覆盖率结果。

## 文件校验

| 项目 | 值 |
|---|---|
| 原始文件 | `2026-08-12 13-36-22.mp4` |
| 时长 | 约 9 分 11 秒 |
| 分辨率 | 1920 × 1080 |
| 文件大小 | 423,002,837 bytes（403.41 MiB） |
| SHA-256 | `C92BD73D5675788F3735DCB26E687DAD66645B5F75B7271E443EF08901AFA917` |

由于文件超过普通 Git 的 100 MiB 限制，视频不进入源码提交，而是作为 GitHub Release `v0.1.0` 的附件发布。Release 链接创建后会同步到本页和 README。

逐分钟演示流程见 [RECORDING_SCRIPT.md](RECORDING_SCRIPT.md)。
