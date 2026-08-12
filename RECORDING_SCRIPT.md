# GlassBox Agent 录屏脚本

目标时长：6～8 分钟。全程使用真实 DeepSeek API；Replay 本身完全离线。

## 录制前检查

- 终端字号调整到 18～20，窗口最大化。
- 关闭微信、邮箱和系统通知，隐藏浏览器收藏栏与个人账号信息。
- 不打开 `.env`，不执行会打印环境变量的命令。
- OBS/录屏软件只捕获项目终端或指定窗口，不捕获整个桌面。
- 网络保持连接，直到脚本明确要求断网。
- 在项目根目录启动 PowerShell，先不要执行命令。

录制开始后执行：

```powershell
.\start-recording.ps1
```

脚本会创建全新的时间戳数据库，运行 `doctor`，然后进入 `chat`。它只显示数据库路径和演示配置，绝不会打印 API Key。

## 逐段操作与讲稿

### 0:00～0:30：项目定位与 Doctor

讲稿：

> 这是 GlassBox Agent，一个不依赖现有 Agent 框架、由我自行实现 Runtime 的最小终端 Agent。它的差异点是 append-only 事件账本，同一套 reducer 同时服务于运行、恢复、Context、Replay 和 Fork。启动脚本首先使用真实 DeepSeek API 做无副作用探针。

屏幕出现 `Doctor passed` 后指出：模型名、工具名、参数 JSON 和 `tool_call_id` 均验证通过。

### 0:30～1:00：无工具直答

输入：

```text
请用一句话说明什么是 Agent Runtime，不要调用工具。
```

讲稿：

> 模型可以直接回答，也可以自主决定调用工具；Runtime 不会强迫所有问题走工具链。

### 1:00～2:20：多工具链、受控故障与 Trace

输入：

```text
查询公司上海差旅餐补政策，计算出差三天的餐补总额，并添加待办“周五提交报销”。
```

任务执行时，`chat` 默认只在结束后显示最终答案，不会实时刷出内部工具链。最终答案出现后，立即输入：

```text
/trace
```

等待模型执行时的讲稿：

> 当前 Chat UI 只展示最终回答，工具调用不会在执行时逐条刷屏。完成后我会打开事件 Trace，用持久化记录验证真实执行过程，而不是只根据最终答案推测模型做了什么。

最终答案出现后，应能看到 360 元和“周五提交报销”。打开 `/trace` 后，按事件顺序讲解：

> 第一次 `search_docs` 返回了 `retryable=true` 的受控错误。Runtime 没有在后台静默重放业务工具，而是把错误作为结构化 `tool_result` 回灌模型。模型随后重新调用 `search_docs`，再执行 `read_doc`、`calculator` 和 `todo`，最后生成答案。这里能看到每条事件的 ID、turn 和 sequence；原始 call ID 也保存在事件 payload 中，因此可以审计和 replay。

在 Trace 中用鼠标依次指出：

```text
tool_requested(search_docs)
→ tool_failed(DEMO_TRANSIENT_FAILURE)
→ tool_requested(search_docs)
→ tool_succeeded
→ read_doc
→ calculator
→ todo
→ assistant_message（360 元）
```

如果模型没有重试，直接说明错误是可观察的，然后重新输入同一请求；不要临时修改代码。

### 2:20～3:00：触发并验证压缩

输入：

```text
列出当前待办，并用一句话确认三天餐补总额。
```

这一轮完成后再次输入：

```text
/trace
```

讲稿：

> 演示环境把 Context 阈值降低到 2200 字符并只保留最近一轮，因此第三轮开始前会压缩已经完成的旧 turn。现在 Trace 中新增了 `context_compacted`，而前面展示过的原始工具事件仍然存在，说明压缩只改变后续模型 Context，没有删除历史。

在 trace 中指出：

- `context_compacted`；
- 它覆盖到的 `through_event_id`；
- `assistant_message`；
- 每条事件的 ID、turn 和顺序。

再输入：

```text
/context
```

讲稿：

> `/context` 展示的是下一次真正发送给模型的活动 Context，其中包含结构化 Memory、todo 投影和最近完整轮次。原始事件继续保存在账本中，因此 Context 压缩和历史保存是两件独立的事情。

### 3:00～4:00：Session 隔离

先从 `/sessions` 输出中记住当前 Session A 的 UUID，然后输入：

```text
/new Session-B
```

```text
添加待办“准备架构题讲解”，然后列出当前待办。
```

```text
/replay
```

讲稿：

> Session B 只看到自己的待办。现在切回 Session A，检查状态没有串扰。

输入：

```text
/sessions
```

从列表复制 Session A 的 UUID：

```text
/use <SESSION_A_ID>
```

```text
/replay
```

预期：Session A 只有“周五提交报销”，不会出现“准备架构题讲解”。

### 4:00～5:20：Fork 与父子独立演进

在 Session A 中输入：

```text
/trace
```

选择 trace 中最后一个 `assistant_message` 的 ID，必须是已完成 turn，然后输入：

```text
/fork <ASSISTANT_MESSAGE_EVENT_ID>
```

子 Session 中输入：

```text
在继承方案基础上改为五天，重新计算餐补，并新增待办“确认五天差旅方案”。
```

随后输入：

```text
/replay
```

讲稿：

> Fork 继承分叉点之前的事件投影，但之后写入独立事件流。子 Session 现在继承原待办并增加新待办，五天餐补应为 600 元。

输入 `/sessions`，复制父 Session A 的 UUID并切回：

```text
/use <SESSION_A_ID>
```

```text
/replay
```

预期：父 Session 仍只有原待办，没有子分支新增项。

### 5:20～6:00：真正断网后的 Offline Replay

此时不再需要模型调用。打开 Windows 快捷设置，临时关闭 Wi-Fi，然后回到终端输入：

```text
/replay
```

讲稿：

> 当前网络已经关闭。Replay 不调用模型，也不重新执行工具，而是从历史事件经过同一个 reducer 重建状态，所以可以离线恢复。这里的确定性指状态投影确定，不宣称模型输出能够重新生成得完全一致。

展示完成后重新打开 Wi-Fi。

### 6:00～6:50：测试与总结

输入：

```text
/quit
```

然后执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --cov=glassbox --cov-branch --cov-report=term --cov-fail-under=90
```

讲稿：

> 项目覆盖 Provider、Registry、Runtime、Session、Reducer、Fork、压缩、存储和安全边界。当前共 39 个测试，分支覆盖率 96.27%。GlassBox 的重点不是功能堆叠，而是用很小的 Runtime 给出可观察、可恢复、可验证的 Agent 执行语义。

## 现场异常处理

- `doctor` 失败：立即停止录制，不展示或检查 `.env`，在录屏外修复后重新开始。
- 模型输出措辞不同：只检查事实、工具集合和最终状态，不要求逐字一致。
- 模型未恢复受控故障：再次发送相同业务请求，并说明错误已被显式记录。
- 未出现 `context_compacted`：再发送一句“请总结当前任务状态”，然后重新查看 `/trace`。
- Fork 报错：确认使用的是 `assistant_message` 行的 ID，而不是 `tool_succeeded` 或 `context_compacted` ID。
- 断网前仍有模型任务：等待回答完成后再关闭网络。

## 录制后检查

- 视频中没有出现 `.env`、API Key、个人通知或账号信息。
- 终端文字在 1080p 播放时可读。
- `doctor`、真实工具链、错误恢复、Session 隔离、压缩、Replay、Fork 和覆盖率均出现。
- 视频链接使用无须申请权限即可查看的分享设置。
