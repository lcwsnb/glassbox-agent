# GlassBox Agent 无旁白录屏流程

目标时长：6～8 分钟。视频不需要讲话，通过“标题卡 + 终端真实输出”完整表达设计。

## 屏幕安排

- 主屏：浏览器标题卡与 PowerShell 终端，OBS 只录这一块屏幕。
- 副屏：OBS、本文档、计时器和需要复制的 Session ID。
- 浏览器打开 `silent-demo-cards.html`，按 `F11` 全屏，使用左右方向键换页。
- PowerShell 在项目根目录打开并最大化，终端字号建议 18～20。
- 标题卡每张停留 3～5 秒，然后 `Alt+Tab` 切换终端进行操作。

> 注意：无需把所有输入速度完整录下来。可以提前把下一条命令放在副屏文档中，正式录制时复制到主屏终端。

## 镜头流程

| 时间 | 主屏画面 | 操作 | 建议停留 |
|---|---|---|---|
| 0:00 | 标题卡 1：GlassBox Agent | 无操作 | 5 秒 |
| 0:05 | 标题卡 2：Compatibility Probe | 右方向键 | 4 秒 |
| 0:09 | 终端 | 执行 `start-recording.ps1`，展示 `Doctor passed` | 15～25 秒 |
| 0:35 | 标题卡 3：Model Decision | 切回浏览器、右方向键 | 4 秒 |
| 0:39 | 终端 | 无工具直答 | 15 秒 |
| 0:55 | 标题卡 4：Multi-tool Recovery | 右方向键 | 5 秒 |
| 1:00 | 终端 | 执行差旅多工具任务 | 40～70 秒 |
| 2:00 | 标题卡 5：Flight Recorder | 右方向键 | 4 秒 |
| 2:04 | 终端 | `/trace` | 20 秒 |
| 2:25 | 标题卡 6：Context Compaction | 右方向键 | 4 秒 |
| 2:29 | 终端 | 发送第三轮并展示 `/trace`、`/context` | 30 秒 |
| 3:00 | 标题卡 7：Session Isolation | 右方向键 | 4 秒 |
| 3:04 | 终端 | 创建 B、添加待办、切回 A、两次 Replay 对比 | 60 秒 |
| 4:05 | 标题卡 8：Replay & Fork | 右方向键 | 4 秒 |
| 4:09 | 终端 | 从最后一个 assistant_message Fork，计算五天方案 | 60～80 秒 |
| 5:20 | 终端 | 断网后 `/replay`，再切回父 Session 对比 | 25 秒 |
| 5:45 | 标题卡 9：Verification | 右方向键 | 4 秒 |
| 5:49 | 终端 | `/quit`，运行测试与覆盖率 | 30 秒 |
| 6:20 | 标题卡 10：结束页 | 右方向键 | 6 秒后停止录制 |

## 终端输入清单

### 启动与 Doctor

```powershell
.\start-recording.ps1
```

### 直答

```text
请用一句话说明什么是 Agent Runtime，不要调用工具。
```

### 多工具链与受控错误

```text
查询公司上海差旅餐补政策，计算出差三天的餐补总额，并添加待办“周五提交报销”。
```

预期最终事实为 360 元，并创建“周五提交报销”。首次 `search_docs` 会失败，随后由模型恢复。

### Trace 与压缩

```text
/trace
```

```text
列出当前待办，并用一句话确认三天餐补总额。
```

```text
/trace
```

```text
/context
```

Trace 中应出现 `tool_failed`、后续 `tool_succeeded` 和 `context_compacted`。

### Session 隔离

先执行 `/sessions`，把当前 Session A ID 复制到副屏便签：

```text
/sessions
```

```text
/new Session-B
```

```text
添加待办“准备架构题讲解”，然后列出当前待办。
```

```text
/replay
```

```text
/use <SESSION_A_ID>
```

```text
/replay
```

画面应显示 B 只有“准备架构题讲解”，A 只有“周五提交报销”。

### Fork

```text
/trace
```

在副屏记下最后一条 `assistant_message` 的事件 ID：

```text
/fork <ASSISTANT_MESSAGE_EVENT_ID>
```

```text
在继承方案基础上改为五天，重新计算餐补，并新增待办“确认五天差旅方案”。
```

```text
/replay
```

预期子 Session 显示 600 元，并拥有两条待办。随后切回父 Session：

```text
/sessions
```

```text
/use <SESSION_A_ID>
```

```text
/replay
```

父 Session 仍只有原待办。

### 离线 Replay

确保没有模型请求正在执行，在副屏关闭 Wi-Fi，再回到主屏输入：

```text
/replay
```

成功显示状态后重新开启 Wi-Fi。

### 测试与覆盖率

```text
/quit
```

```powershell
.\.venv\Scripts\python.exe -m pytest -q --cov=glassbox --cov-branch --cov-report=term --cov-fail-under=90
```

## 让无旁白视频更易懂

- 标题卡与对应终端操作必须交替出现，不要连续播放完所有标题卡。
- 鼠标在关键行附近停留 1～2 秒，例如 `Doctor passed`、`tool_failed`、`context_compacted`、两个 Todo 和覆盖率。
- `/trace` 内容过长时缓慢滚动，不要快速跳过。
- 等模型期间保留终端画面即可；超过 10 秒可以在后期加速 2～3 倍。
- 最终视频可以在 Clipchamp 中删除长等待段，但不要改变事件顺序。
- 如果愿意做少量后期，可加非常轻的背景音乐；音量不能影响观看者专注终端内容。

## 录制前最后检查

- OBS 来源只选择主屏，副屏不在预览中。
- 浏览器标题卡已按 `F11` 全屏。
- Windows 勿扰已开启。
- `.env` 未打开，终端历史中没有打印密钥的命令。
- 先录 15 秒测试，确认标题卡和终端文字在 1080p 下清晰。
