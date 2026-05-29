# OpenAkita 对话机制与完整工作流程

本文专门解释 OpenAkita 的“对话机制”。

这里的“对话”不是传统意义上的“用户发一句，模型回一句”，而是一个带有：

- 会话恢复
- 并发控制
- 意图分析
- Prompt 组装
- 推理循环
- 工具执行
- 安全确认
- 用户追问中断
- Session / Memory 持久化
- SSE 恢复与补流

的完整任务流水线。

如果只用一句话概括：

> OpenAkita 的聊天，本质上是“以对话为入口的任务执行系统”。

## 1. 先建立整体心智模型

一轮完整对话大致分为 8 个阶段：

```text
1. API 收到消息
2. 检查会话并发 / 风险确认 / turn 幂等
3. 取回或创建该会话专属 Agent
4. 构造本轮可供 LLM 使用的上下文
5. 调用 ReasoningEngine 进入 ReAct/Ralph 循环
6. 流式输出 thinking / 文本 / tool / ask_user / security_confirm
7. 收尾：保存 assistant 回复、工具摘要、usage、artifact
8. 更新 session / memory / checkpoint / lifecycle，并支持 resume
```

所以 `/api/chat` 只是入口，真正的主链路在 `Agent.chat_with_session_stream()` 和
`ReasoningEngine.reason_stream()`。

## 2. API 入口层

桌面端/Web 端主入口是：

- `POST /api/chat`：SSE 流式对话
- `POST /api/chat/sync`：非流式对话
- `GET /api/chat/resume`：对正在执行中的 SSE 补流
- `POST /api/chat/cancel`：取消当前任务
- `POST /api/chat/skip`：跳过当前步骤
- `POST /api/chat/insert`：向正在运行的任务插入用户消息
- `POST /api/chat/answer`：回答 `ask_user` 或安全确认

从设计上看，对话接口已经不是单一 endpoint，而是一组“任务生命周期控制接口”。

## 3. `/api/chat` 收到请求后的第一层处理

### 3.1 基础校验

`/api/chat` 会先做几件基础事：

- 检查 `conversation_id`
- 检查消息是否为空
- 生成 `request_id`
- 判断是否带附件

这里一个关键点是：

- 在 agent pool 模式下，`conversation_id` 是必须的
- 因为每个会话会绑定一个专属 Agent 实例，缺失会导致实例泄漏或状态串话

### 3.2 风险确认回复优先处理

如果用户这次发来的内容，其实是在回应上一轮的高风险确认，那么 `/api/chat` 不会把它当普通消息处理，而会先走挂起的 risk answer 处理逻辑。

这意味着用户输入在进入模型前，先经过“这是不是在回答系统上一次确认框”的判断。

### 3.3 turn 幂等控制

如果客户端提供 `turn_id`，服务端会做一轮幂等检查：

- 同一个 `turn_id` 正在执行：直接返回 409
- 同一个 `turn_id` 已成功或已失败：也返回 409

目的很直接：

- 防止前端重试、断网重发、SSE 重连把同一轮对话重复提交两次
- 防止 session 历史被重复写脏

### 3.4 会话 busy-lock 与 double-texting 策略

OpenAkita 非常重视“同一会话不能同时被两个请求改写”。

所以 `/api/chat` 在真正开始之前，会通过 `ConversationLifecycleManager` 获取会话级 busy lock。

它支持多种策略：

- `REJECT`：如果会话忙，直接拒绝
- `QUEUE`：排队等待上一轮结束
- `STEER`：把本次消息插入到当前运行中的任务上下文里

这一步非常关键，因为它把“聊天并发问题”提升到了协议层，而不是交给前端碰运气处理。

## 4. 获取会话专属 Agent

`_get_agent_for_session()` 会优先从 `agent_pool` 中按 `conversation_id` 获取或创建 Agent。

这意味着：

- 不同会话通常有不同 Agent 实例
- 会话级状态不会简单共享一个全局 Agent
- 执行中的推理链、工具缓存、待确认状态、任务状态能与会话绑定

这也是 OpenAkita 能支持并发多会话流式执行的基础之一。

## 5. `_stream_chat()` 的职责

`chat.py` 里的 `_stream_chat()` 是一个“薄传输层”。

它自己不做核心推理，只负责：

- 调用 `Agent.chat_with_session_stream()`
- 把内部事件包装成 SSE
- 检测客户端断开
- 收集文本增量
- 转发 artifact
- 捕获 `ask_user`
- 保存 assistant 响应到 session
- 在客户端断开但任务仍继续时，安排后台保存

所以可以把它理解成：

> API 层负责“接流和收尾”，Agent 层负责“真正执行”。

## 6. `Agent.chat_with_session_stream()` 的主流程

这是 OpenAkita 对话机制的核心入口。

### 6.1 初始化与停止指令短路

如果用户输入本身是停止命令，例如“停止”“cancel”之类：

- 不进入 LLM
- 直接取消当前任务
- 返回 `todo_cancelled + text_delta + done`

这说明“停止”不是 prompt 行为，而是运行时控制行为。

### 6.2 解析 `conversation_id`

Agent 会把：

- `session_id`
- `conversation_id`

都绑定到当前执行上下文，后续取消、跳过、session 清理、memory 关闭都靠这个 ID 串起来。

### 6.3 预占或排队旧任务

在进入本轮推理前，会调用 `_preempt_or_queue_prev_task()`。

它的目的不是简单检查“有没有旧任务”，而是处理：

- 老任务继续跑
- 新任务抢占老任务
- 新消息进入排队
- 任务时间线里插入中断 marker

因此会话不是“后来的消息直接覆盖前面的消息”，而是有明确的并发治理策略。

### 6.4 安装 PolicyContext

在 stream 路径里，Agent 会构建并安装本轮 `PolicyContext`。

这个上下文会携带：

- session / conversation 信息
- mode
- user_message
- channel
- 是否子 Agent
- 父子委派链信息

后续工具执行时的权限判断、安全确认、allowlist、sandbox 都依赖这个上下文。

## 7. `_prepare_session_context()`：对话准备阶段

这是理解 OpenAkita 对话机制的关键方法。

它并不是简单拿历史消息拼一拼，而是做了一整套“上下文整形”。

### 7.1 对齐 Memory Session

先把当前会话对齐到 `memory_manager`：

- 根据 `session_key` / `conversation_id` 生成 memory-safe id
- 启动记忆 session
- 绑定 workspace id
- 重置 scratchpad，避免跨会话污染

这说明：

- 聊天历史和长期记忆虽然相关，但不是同一份数据
- 对话开始前必须先对齐记忆上下文

### 7.2 注入 IM / Gateway 上下文

如果请求来自 IM 通道，还会设置 IM context。

这样 `ask_user`、安全确认、媒体转写、消息回发都能正确走通道能力。

### 7.3 当前 user turn 先记到 memory

用户消息会被先记录为本轮 memory turn：

- `record_turn("user", message)`

同时还可能触发：

- `working_facts` 提取
- `trait_miner` 人格/偏好挖掘

所以用户输入不只是“给模型看”，也会影响长期记忆与用户画像。

### 7.4 意图分析

接着进入 `IntentAnalyzer`。

它会判断当前消息更像：

- 闲聊
- 任务
- 分析
- 是否需要工具
- 是否需要 todo / plan
- 是否更偏证据导向

这个结果会影响：

- 本轮 mode 的实际执行方式
- 工具可用范围
- 是否强制 todo
- Prompt 里的任务定义

也就是说，OpenAkita 不是用户选了 `mode=agent` 就固定按 agent 跑，中间还有一层意图驱动路由。

### 7.5 话题切换检测

IM 通道会做 topic change detection。

如果判断用户已经明显切换话题：

- 在历史中插入 `[上下文边界]`
- 让 session 标记 topic boundary
- 后台触发记忆提取
- 更新 scratchpad 当前 focus

这里很重要的一点是：

- 插入的边界不一定永久污染最终喂给模型的结构
- 后续还会做角色交替保护和历史清洗

### 7.6 历史消息构造

真正喂给模型的 `messages` 不是 session 的原始消息列表，而是经过整理后的版本。

整理规则包括：

- 去掉最后一条当前 user 消息，避免重复拼接
- 在窗口内去掉近重复消息，防止重连/重试造成重复上下文
- 跳过 `transient` / `transient_for_llm` 消息
- 清理 assistant 消息中的内部 trace marker
- 从 assistant metadata 还原 `tool_summary`
- 给消息补 `[HH:MM]` 时间锚点
- 相邻同角色消息会合并，避免角色交替错误

这意味着：

> Session 历史是“事实记录”，而喂给模型的是“整理后的上下文视图”。

### 7.7 当前轮输入构造

当前用户输入会进一步包装为 `CurrentTurnInput`，并与对象注册表协同：

- 合并最近对象引用
- 注册本轮对象
- 让模型对“这个文件 / 这张图 / 刚才那个对象”有更稳定的承接能力

### 7.8 多模态与附件处理

OpenAkita 的“对话”不仅是文本。

它会根据模型能力，把附件处理成不同形式：

- PDF：原生 document block，或降级为提取文本
- Audio：原生 audio block，或在线 STT 转文字
- Image：vision 模型可直接输入，否则降级文本提示
- Video：视 endpoint 能力决定

然后把这些内容注入当前轮消息。

### 7.9 上下文压缩与任务监控

准备阶段的后半段还会做：

- 上下文压缩
- TaskMonitor 创建

这两个动作的作用分别是：

- 避免超长历史把模型窗口打爆
- 为后续超时、重试、复盘、fallback 提供状态支撑

## 8. System Prompt 的构造

准备完成后，Agent 会调用 `_build_system_prompt_compiled()`。

这一层会把以下信息合成系统提示词：

- identity
- runtime rules
- tools / skills / MCP catalog
- session 类型
- 当前 task description
- mode
- memory 片段
- 用户/会话个性化信息

然后还会额外注入本轮 `TaskDefinition`。

这意味着系统提示词不是固定模板，而是：

> 会根据当前会话、模式、任务、记忆和扩展能力动态编译出来。

## 9. `ReasoningEngine.reason_stream()`：真正的对话执行循环

这一步是“聊天为什么像任务执行”的根本原因。

它不是简单请求一次模型，而是循环执行：

```text
思考 -> 产出决策 -> 执行工具/直接回答 -> 把结果写回 working_messages -> 下一轮思考
```

### 9.1 先进入 thinking 阶段

流式推理开始时，会先发：

- `thinking_start`
- `thinking_delta`
- `thinking_end`

这里的 thinking 不是最终回答，而是链路中的“思考态可视化”。

### 9.2 模型决策有三类结果

从整体行为上看，本轮模型决策主要会落入三类：

- 直接回答文本
- 发起一个或多个工具调用
- 调用 `ask_user`

每一轮都会把结果写进 `working_messages`，供下一轮继续推理。

### 9.3 文本输出

如果模型本轮主要是生成回答文本：

- 流式 `text_delta`
- 可能在必要时发 `text_replace`
- 最终文本会累积为 `_full_reply`

### 9.4 工具调用

如果模型决定调用工具：

- 先发 `tool_call_start`
- 执行前经过 Policy V2 检查
- 再发 `tool_call_end`
- 工具结果作为 `tool_result` 回写到 working messages

工具执行时还有很多保护：

- mode guard
- 同名工具调用频率限制
- 相同调用参数频率限制
- 只读工具缓存复用
- 取消 / skip 中断
- 安全确认 / defer

这说明 OpenAkita 的工具调用不是“模型说用就用”，而是受运行时治理的。

## 10. `ask_user` 与 `security_confirm` 的区别

这两个事件表面都在“等用户”，但语义完全不同。

### 10.1 `ask_user`

`ask_user` 是模型主动向用户补信息：

- 缺关键输入
- 有多个方案要用户选择
- 需要进一步澄清意图

当模型调用 `ask_user`：

- ReasoningEngine 会先执行本轮其它非 `ask_user` 工具
- 再发出 `ask_user` 事件
- 设置退出原因为 `ask_user`
- 当前任务进入 `WAITING_USER`
- 立刻 `done`

注意：

- 这里的 `done` 不代表任务真正完成
- 它只是“本轮流结束，等待用户下一条消息继续接力”

### 10.2 `security_confirm`

`security_confirm` 是策略层要求用户授权高风险工具调用：

- 删除/覆盖文件
- 危险 shell
- 控制面变更
- 可能造成破坏的动作

当工具被判定为 `CONFIRM`：

- 不会直接执行
- 会发出 `security_confirm` 事件
- UI/IM 等待用户选择 allow / deny / sandbox 等选项
- 之后再恢复该工具执行或拒绝

所以：

- `ask_user` 是“模型需要更多信息”
- `security_confirm` 是“系统需要用户授权”

这是 OpenAkita 对话机制里非常重要的分层。

## 11. 工具结果如何影响下一轮对话

工具执行后，结果不会只显示给前端，还会被重新写回推理上下文。

因此下一轮模型看到的是：

- 它刚才说了什么
- 它刚才调用了什么工具
- 每个工具返回了什么

这就形成了典型 ReAct 循环：

```text
Thought -> Action -> Observation -> Next Thought
```

OpenAkita 之所以能连续多步工作，就是因为每轮 observation 都会进入下一轮 reasoning。

## 12. 流式事件层怎么和 UI 对齐

前端/桌面端看到的不是一个整块响应，而是一组事件。

常见事件类型包括：

- `heartbeat`
- `thinking_start` / `thinking_delta` / `thinking_end`
- `text_delta`
- `text_replace`
- `chain_text`
- `tool_call_start`
- `tool_call_end`
- `context_compressed`
- `security_confirm`
- `ask_user`
- `todo_created` / `todo_step_updated` / `todo_completed`
- `agent_handoff`
- `artifact`
- `error`
- `done`

其中：

- `text_delta` 面向最终自然语言输出
- `chain_text` 更像执行轨迹说明
- `tool_call_*` 是动作可视化
- `done` 表示本轮 SSE 结束

所以 UI 展示的是“执行过程”，不是单纯回答文本。

## 13. Session 保存：这轮对话最终怎么落库

SSE 传输层 `_stream_chat()` 在尾部负责把这一轮 assistant 回复保存到 session。

保存的不是纯文本，还包括 metadata：

- `chain_summary`
- `tool_summary`
- `artifacts`
- `usage`
- `ask_user`
- `is_truncated`
- `stream_error`

保存文本时还会区分：

- 正常回答：保存 `_full_reply`
- `ask_user`：优先保存“LLM 文本 + 问题 + 选项”
- 中途失败：带截断标记
- 已取消：保存 `[任务已取消]`

这一步很重要，因为它决定了：

- 下一轮模型能否看到上轮确认问题
- 前端历史时间线是否完整
- 工具轨迹是否可重建

## 14. `_finalize_session()`：Agent 层收尾

Agent 在推理结束后，还会做一层更深的收尾。

主要包括：

- 固化 `_last_react_trace`
- 提取 usage summary
- 生成 chain summary
- 完成 TaskMonitor
- 必要时安排后台 retrospect
- 将 assistant turn 写入 memory
- 汇总所有 tool_calls / tool_results
- 自动关闭 todo / plan
- 调用 `memory_manager.end_session()`
- 标记 agent run lifecycle 完成

换句话说：

- `_stream_chat()` 负责 session 视角的历史保存
- `_finalize_session()` 负责 Agent / memory / monitor 视角的运行收尾

两者不是重复，而是两层不同粒度的落地。

## 15. `_cleanup_session_state()`：为什么每轮结束都要重置

收尾后还会清理很多临时状态：

- 当前任务定义
- 当前用户消息
- 当前 session 引用
- Policy V2 会话状态
- UI confirm bus
- pending confirms
- todo 状态
- session log buffer
- reasoning engine 大对象缓存

这一步是为了防止：

- 上一轮取消状态泄漏到下一轮
- 大对象长期占内存
- pending confirm 误伤后续会话
- plan 状态越积越多

所以 OpenAkita 的对话是“有生命周期的执行单元”，不是一团长期不清的全局状态。

## 16. 长任务、断连与后台保存

如果客户端先断开，但 Agent 任务还没跑完，`_stream_chat()` 不会立刻丢掉结果。

它会：

- 等一段时间看任务是否结束
- 如果仍未结束，注册后台 drain-and-save 回调
- 等任务真正跑完后把结果补存到 session

这意味着：

- SSE 断开不等于任务丢失
- 用户刷新页面后仍可能在历史中看到最终结果

这是典型“前端连接生命周期”和“后端任务生命周期”分离的设计。

## 17. `/api/chat/resume`：对话补流机制

很多系统的 SSE 一断就断了，但 OpenAkita 还有专门的补流接口：

- `GET /api/chat/resume?conversation_id=...&since_seq=...`

它的定位是：

- 只读附着到正在执行中的会话流
- 从 ringbuffer 回放 `since_seq` 之后的事件
- 持续轮询尾部新事件
- 会话空闲后自动结束

注意这个接口：

- 不重新触发 Agent
- 不抢 busy-lock
- 不新建 turn
- 只负责“把已有事件继续送给你”

所以 OpenAkita 的“恢复对话”不是重新跑一遍模型，而是继续接上原来的事件流。

## 18. 对话过程中的用户干预

用户并不只能被动等待回复。

### 18.1 取消

`POST /api/chat/cancel`

作用：

- 终止当前任务
- 立即释放 busy-lock

### 18.2 跳过

`POST /api/chat/skip`

作用：

- 跳过当前步骤或工具
- 任务本身不一定终止

### 18.3 插入消息

`POST /api/chat/insert`

作用：

- 向正在运行中的任务插入额外用户信息

而且它会先智能判断：

- 如果你插入的是“停止”，转成 cancel
- 如果你插入的是“跳过”，转成 skip
- 否则才作为普通 user insert 写进运行态

这说明 OpenAkita 的对话不是“等本轮结束后再说”，而是支持运行中操控。

## 19. `/api/chat/sync` 和流式对话的区别

`/api/chat/sync` 不是简单把 SSE 改成一次性返回。

它还有一个重要语义：

- 会话被标记为 unattended

这会导致确认类工具不等待用户弹窗，而是进入 `DeferredApprovalRequired` 路径。

因此：

- `/api/chat` 适合交互式产品界面
- `/api/chat/sync` 适合脚本、CI、REST 客户端

二者执行主线相近，但“等待用户确认”的处理方式不同。

## 20. 哪些消息会进入模型，哪些不会

这是理解对话质量的关键。

### 会进入模型的内容

- 清洗后的历史 user / assistant 消息
- 还原后的工具摘要
- 当前轮文本
- 当前轮多模态块
- TaskDefinition
- system prompt 中的 identity / tools / skills / memory / rules

### 不会进入模型的内容

- `transient` / `transient_for_llm` 消息
- 某些内部 trace marker
- 仅用于 UI 的风险确认回执
- 某些被清洗掉的重复消息

也就是说，UI 看见的聊天记录和模型真正看到的上下文，不一定一模一样。

这是设计使然，不是 bug。

## 21. 一轮典型对话时序图

```text
User
  -> POST /api/chat
  -> Chat API 校验 / risk replay / turn 幂等 / busy-lock
  -> 获取 conversation 专属 Agent
  -> _stream_chat()
  -> Agent.chat_with_session_stream()
  -> _prepare_session_context()
      -> memory session 对齐
      -> user turn 记忆
      -> intent 分析
      -> 历史清洗与构造
      -> 多模态处理
      -> TaskMonitor 创建
  -> build system prompt
  -> ReasoningEngine.reason_stream()
      -> thinking_start / delta / end
      -> 直接文本输出
      -> 或 tool_call_start / tool_call_end
      -> 或 security_confirm
      -> 或 ask_user
      -> working_messages 更新，进入下一轮
  -> _finalize_session()
      -> chain summary / usage / memory / plan close
  -> _stream_chat 保存 assistant 消息到 session
  -> SSE done
  -> lifecycle.finish()
```

## 22. 这个对话机制的本质特点

把整个流程串起来后，可以看到 OpenAkita 的对话机制有 6 个鲜明特点：

### 22.1 会话不是字符串历史，而是运行态容器

它不仅保存消息，还保存：

- 当前 profile
- handoff 记录
- sub-agent records
- checkpoints
- focus terms
- metadata

### 22.2 一轮“聊天”本质上是任务生命周期

里面包含：

- 并发控制
- 安全治理
- 工具执行
- 中断控制
- 后台收尾

### 22.3 模型并不直接面对原始历史

历史会经过：

- 去重
- 标记清洗
- 摘要回放
- 多模态重写
- 角色保护

### 22.4 对话是可以暂停和恢复的

通过：

- `ask_user`
- `security_confirm`
- `/api/chat/resume`
- checkpoints

### 22.5 用户可以在运行中干预

通过：

- cancel
- skip
- insert
- steer

### 22.6 对话和长期记忆是联动的

不是聊完才统一归档，而是在准备和收尾阶段都与 memory 交互。

## 23. 最终结论

OpenAkita 的“对话机制”可以概括为：

> 以会话为边界、以 Agent 为执行体、以 ReasoningEngine 为循环核心、以 Policy 和 Memory 为底层约束、以 SSE 为可视化协议的任务型对话系统。

因此它的聊天工作流不是：

```text
user -> llm -> assistant
```

而更接近：

```text
user
 -> conversation lifecycle
 -> session + memory alignment
 -> intent analysis
 -> prompt assembly
 -> reasoning loop
 -> tool execution / approval / ask_user
 -> streaming events
 -> session + memory persistence
 -> resume / control / continuation
```

如果你接下来要继续深挖，对理解最关键的源码顺序建议是：

1. `src/openakita/api/routes/chat.py`
2. `src/openakita/core/agent.py`
3. `src/openakita/core/reasoning_engine.py`
4. `src/openakita/sessions/session.py`
5. `src/openakita/sessions/manager.py`
6. `src/openakita/core/policy_v2/`
7. `src/openakita/memory/`
