# 调用链可靠性与兼容性改造实施计划

## 1. 文档目的

本文件用于承接 `docs/callchain-reliability-design.md`，将设计方案拆解为可执行的开发实施任务。

本文档重点回答：

1. 先做什么
2. 后做什么
3. 每一步改哪些文件
4. 每一步怎么测试
5. 每一步如何验收
6. 如果出问题如何回退

---

## 2. 实施原则

整体采用：

- 小步快跑
- 每步可回退
- 每步可验证
- 先低破坏修复，再做协议统一，再做性能与治理

每个阶段都必须满足：

1. 改动范围明确
2. 回归测试可执行
3. 日志和真实场景至少验证一轮
4. 必要时有 feature flag 或可局部回退策略

并补充两条约束：

1. 不重复打开已完成收口的“Todo 可见化统一”和“CLI Todo 独立面板”问题
2. 所有“协议修复”都优先采用读兼容、写规范化的两阶段推进方式

---

## 3. 总体阶段划分

建议拆为 5 个阶段。

## 阶段 0：契约核对与基线冻结

### 目标

在正式改代码前，先确认当前系统哪些行为已经稳定、哪些是真正待改边界。

### 范围

- Desktop 前端对 `/api/files` / `file_url` / receipts 的消费契约
- Todo 非 planner 可见化链路
- Planner 场景 Todo 独立面板展示
- 现有 response verify / consistency guard 行为边界

### 交付标准

1. 明确 frontend 是否已经消费 `file_url`
2. 明确本轮不再改的已收口内容
3. 为后续阶段建立真实回归样本

### 说明

这一阶段不做功能改造，只做基线确认。其价值在于避免后续把“后端协议问题”和“前端未消费问题”混为一谈。

## 阶段 A：交付可靠性修复

### 目标

优先修复用户最容易感知的问题：

1. 附件生成了但没显示
2. 截图工具第一次失败
3. 交付状态不清楚

### 范围

- `src/openakita/tools/handlers/im_channel.py`
- `src/openakita/tools/handlers/browser.py`
- `src/openakita/tools/browser/playwright_tools.py`
- `src/openakita/core/reasoning_engine.py`
- `src/openakita/core/response_handler.py`

### 交付标准

1. legacy `file_path` 调用不再导致 `missing_artifacts`
2. browser screenshot 自动创建目录
3. 最终结果能区分“本地保存”和“已交付”

## 阶段 B：Todo 协议统一

### 目标

修复 Todo 内部协议不一致，保证 TaskPlanner 与 UI 状态一致。

### 范围

- `src/openakita/tools/handlers/todo_handler.py`
- `src/openakita/core/agent.py`
- `src/openakita/core/task_planner.py`
- `src/openakita/cli/stream_renderer.py`

### 交付标准

1. `update_todo_step` 不再出现“未找到步骤”
2. planner 场景下 Todo 进度与实际执行一致
3. UI 中步骤状态稳定刷新

## 阶段 C：交付状态与通道能力治理

### 目标

让“通道在线”“附件送达”“能力降级”三件事在日志、UI、验证中明确可区分。

### 范围

- `src/openakita/tools/handlers/im_channel.py`
- `src/openakita/channels/adapters/feishu.py`
- `src/openakita/core/response_handler.py`
- `src/openakita/core/validators.py`
- 必要时补充 CLI / Desktop 展示

### 交付标准

1. desktop / IM / cross-channel 的 delivery state 统一
2. Feishu CardKit 降级状态明确
3. response verify 不再把“本地有文件但未送达”和“一般失败”混为一类

## 阶段 D：性能与租户治理

### 目标

在功能链路稳定后，再控制性能和长期安全风险。

### 范围

- `src/openakita/core/agent.py`
- `src/openakita/prompt/builder.py`
- `src/openakita/prompt/budget.py`
- `src/openakita/tools/catalog.py`
- `src/openakita/memory/manager.py`
- session/tenant 入口代码

### 交付标准

1. 复杂任务 token / latency 收敛
2. `default/default` 长期记忆 fallback 下降
3. 启动 warning 更聚焦

---

## 4. 详细任务拆解

## 任务包 0：前后端与现有链路契约核对

### 目标

先冻结当前有效基线，避免实施过程中误改已稳定链路。

### 修改文件

本任务包原则上不改业务代码，如需补文档或诊断日志，单独提交。

### 具体任务

1. 核对 desktop 前端当前是否已根据 `file_url` / receipts 内联展示附件
2. 核对现有 `deliver_artifacts` 在 desktop / IM / cross-channel 三种模式下的返回结构
3. 核对“非 planner 的 todo_required 任务先显示 Todo”是否仍稳定
4. 核对“planner 任务 + Todo 独立面板”是否仍稳定
5. 记录本轮明确不再动的已收口能力：
   - 可见 Todo bootstrap
   - CLI Todo 面板布局
   - Feishu / IM 启动链幂等
   - restart loop race 修复

### 测试要求

1. 一轮 desktop 附件展示真实验证
2. 一轮非 planner Todo 真实验证
3. 一轮 planner Todo 真实验证

### 验收

形成一份当前契约结论，供 A/B/C/D 阶段共同遵守。

---

## 任务包 A1：`deliver_artifacts` legacy 参数兼容

### 目标

允许以下形态被自动归一化：

1. `file_path=/tmp/a.png`
2. `path=/tmp/a.png`
3. `caption=...`
4. `name=...`

### 修改文件

- `src/openakita/tools/handlers/im_channel.py`

### 具体任务

1. 扩展 `_normalize_delivery_params()`
2. 当没有 `artifacts` 时，读取顶层 `file_path/path`
3. 自动构造单元素 manifest
4. 对图片后缀自动判断 `type=image`
5. 增加结构化日志，记录 legacy 参数已归一化
6. 明确保证 receipts 结构只追加字段，不重命名旧字段

### 测试要求

新增/更新单测覆盖：

1. desktop 模式 `file_path` -> `delivered`
2. IM 模式 `file_path` -> receipt 正常
3. 已有 `artifacts` 时保持原行为
4. 非法路径返回结构化错误
5. 旧依赖 receipts 结构的调用方不回归

### 验收

真实场景中不再出现：

- `error_code=missing_artifacts`

作为由 legacy 参数引发的失败。

### 回退

回退 `_normalize_delivery_params()` 增量兼容逻辑即可。

---

## 任务包 A2：截图与文件输出目录自愈

### 目标

让工具自己准备输出目录，不依赖模型补 `mkdir -p`。

### 修改文件

- `src/openakita/tools/handlers/browser.py`
- `src/openakita/tools/browser/playwright_tools.py`
- 如有必要，扩展到其他文件输出工具

### 具体任务

1. 写文件前确保父目录存在
2. 出错时统一返回结构化错误
3. 对相对路径与绝对路径都做处理
4. 补日志，记录 auto-create 行为

### 测试要求

1. 输出目录不存在时，截图仍成功
2. 非法路径时返回稳定错误码
3. 不影响已有成功路径

### 验收

真实浏览器截图任务中，不再需要模型额外调用 shell 创建目录。

### 回退

仅回退该工具内部 mkdir 行为，不影响其他链路。

---

## 任务包 A3：交付状态结构化

### 目标

统一交付状态模型。

### 修改文件

- `src/openakita/tools/handlers/im_channel.py`
- `src/openakita/core/reasoning_engine.py`
- `src/openakita/core/response_handler.py`

### 推荐状态

建议引入：

1. `delivered`
2. `local_only`
3. `frontend_pending`
4. `failed`

### 具体任务

1. desktop 模式成功返回时补 `delivery_state`
2. IM 模式 receipt 解析时补 `delivery_state`
3. reasoning engine 保存最近一次 delivery receipts
4. response handler 按 `delivery_state` 生成更准确结果说明
5. 优先先补日志和验证消费，再决定 UI 字段落位

### 测试要求

1. desktop 成功交付返回 `delivered`
2. 本地文件存在但未送达返回 `local_only`
3. 失败时返回 `failed`
4. 前端/UI 可忽略新字段，不报错
5. consistency / verify 逻辑不再把 `local_only` 误判成 `delivered`

---

## 任务包 B1：Todo Step ID 读兼容

### 目标

先止血，保证旧 `step_1` 也能更新到正确步骤。

### 修改文件

- `src/openakita/tools/handlers/todo_handler.py`

### 具体任务

1. 增加 `_normalize_step_id()`
2. 查找时同时兼容：
   - `1`
   - `step_1`
3. 保留当前 plan 文件格式不变
4. 不修改非 planner 路径的 bootstrap step 协议

### 测试要求

1. 使用 `1` 更新成功
2. 使用 `step_1` 更新成功
3. 恢复旧 plan 后更新仍成功

### 验收

日志中不再出现：

- `❌ 未找到步骤：step_1`

### 回退

回退读兼容逻辑即可。

---

## 任务包 B2：Todo Step ID 写规范化

### 目标

让新链路只写 canonical step id。

### 修改文件

- `src/openakita/core/task_planner.py`
- `src/openakita/core/agent.py`
- `src/openakita/tools/handlers/todo_handler.py`
- `src/openakita/cli/stream_renderer.py`

### 具体任务

1. Planner / TaskExecutor 统一输出 canonical id
2. Todo 事件统一使用 canonical id
3. UI 不依赖 legacy id 形态
4. 不改变“哪些任务进入 TaskPlanner”的现有触发策略

### 测试要求

1. planner 创建 -> update -> complete 全链路一致
2. UI 流式事件不丢步骤状态
3. 旧 plan 恢复仍兼容

### 上线建议

必须在 B1 稳定后再做。

---

## 任务包 C1：Feishu 能力分层与降噪

### 目标

明确“基础消息可用、CardKit 不可用”。

### 修改文件

- `src/openakita/channels/adapters/feishu.py`
- 如有需要，更新 prompt 注入或通道能力展示

### 具体任务

1. 将 CardKit 权限不足缓存为能力状态
2. 减少重复打印长错误
3. 启动摘要中显示 degraded feature

### 测试要求

1. 权限缺失时基础消息能力仍正常
2. CardKit 能力显示为 unavailable
3. 启动日志不再反复刷长段错误细节

---

## 任务包 C2：响应验证与前端展示联动

### 目标

让最终结果更符合真实交付状态。

### 修改文件

- `src/openakita/core/response_handler.py`
- `src/openakita/core/validators.py`
- `src/openakita/cli/stream_renderer.py`
- 如有需要，Desktop 前端对应位置

### 具体任务

1. 区分：
   - 文件已生成但未送达
   - 文件已送达
   - 文件交付失败
2. 在最终结果摘要中明确展示原因
3. 在 CLI 归档记录中保留交付状态摘要
4. 不回退已完成的浏览器一致性提示修复

### 测试要求

1. `deliver_artifacts` 成功时无误导提示
2. 本地文件存在但未送达时给出清晰说明
3. 不再将“本地保存”误报成“已交付”

---

## 任务包 D1：性能缓存第一步

### 目标

先做最低风险缓存。

### 修改文件

- `src/openakita/prompt/builder.py`
- `src/openakita/tools/catalog.py`
- `src/openakita/core/agent.py`

### 具体任务

1. 缓存 session 级稳定 prompt 片段
2. 缓存静态 tool catalog 渲染结果
3. 保证缓存键包含：
   - session
   - profile
   - tool_filter
   - prompt_mode

### 测试要求

1. 缓存命中时结果正确
2. tool_filter 改变时不会串缓存
3. 多 session 不互串

### 上线建议

建议 feature flag 控制，例如：

- `OPENAKITA_ENABLE_PROMPT_CACHE=1`

---

## 任务包 D2：tenant 透传与 memory 安全

### 目标

降低 `default/default` fallback 风险。

### 修改文件

- `src/openakita/memory/manager.py`
- API / CLI / Desktop / IM 入口 session 初始化相关代码

### 具体任务

1. 为 memory start_session 增加 tenant 来源日志
2. 未提供真实 tenant 时：
   - 先禁止长期记忆写入
   - 不中断主任务
3. 增加配置开关：
   - `require_real_tenant_for_long_term_memory`

### 测试要求

1. CLI/desktop 本地模式仍可正常运行
2. API/IM 提供真实 tenant 时正常写 memory
3. 未提供 tenant 时不污染长期记忆

### 上线建议

必须分阶段：

1. 先 warn
2. 再 disable write
3. 最后按需 strict

---

## 5. 测试计划

## 5.1 单元测试

必须补充以下测试组：

1. `deliver_artifacts` 参数兼容
2. desktop receipts 结构
3. browser screenshot 目录自愈
4. Todo step id 双兼容
5. Todo canonical id 全链路
6. response verify 的交付状态区分
7. Feishu 降级能力状态
8. prompt / catalog 缓存隔离
9. memory tenant fallback 安全策略

## 5.2 集成测试

重点集成场景：

1. 浏览器截图 -> desktop inline 展示
2. Todo + TaskPlanner + 子任务执行
3. browser -> screenshot -> deliver_artifacts -> final answer
4. IM 基础消息/文件交付
5. Feishu 卡片不可用时文本降级
6. 非 planner `todo_required` -> visible Todo -> direct execution
7. planner `todo_required` -> visible Todo -> delegated execution

## 5.3 真实验证

建议复用并扩展现有真实验证脚本和日志方案。

必须覆盖：

1. CLI 本地分析类任务
2. 浏览器截图类任务
3. Desktop 文件显示类任务
4. Todo + TaskPlanner 编排任务
5. 出现中断/提前结束条件的任务
6. 非 planner 但 `todo_required=True` 的多步骤任务
7. IM / Feishu 基础消息能力未退化

并增加一条要求：

1. 每个阶段至少复用一份已有真实日志样本，避免设计脱离现网问题。

---

## 6. 建议排期

以下为建议执行节奏，按“开发日”估算。

## 第 0 周

1. 任务包 0 契约核对
2. 冻结本轮不改范围
3. 准备真实回归样本

## 第 1 周

1. A1 `deliver_artifacts` 兼容
2. A2 screenshot 自愈
3. A3 交付状态日志增强
4. 单测 + 一轮真实回归

## 第 2 周

1. B1 Todo step id 读兼容
2. B2 Todo step id 写规范化
3. C2 response/UI 结果状态联动
4. 单测 + CLI/Browser 真实回归

## 第 3 周

1. C1 Feishu 能力分层
2. D1 prompt/tool catalog 轻缓存
3. 一轮性能对比验证

## 第 4 周

1. D2 tenant 透传与 memory 安全开关
2. 启动 warning 聚合
3. 综合回归和发布准备

---

## 7. 验收清单

## 阶段 A 验收

1. `missing_artifacts` 不再由 legacy 参数触发
2. screenshot 不再因目录不存在失败
3. 最终结果能明确说明本地保存/已交付

## 阶段 B 验收

1. Todo 步骤更新无 miss
2. UI Todo 状态与实际执行同步
3. planner 场景闭环可验证

## 阶段 C 验收

1. Feishu 能力降级信息明确
2. response verify 不再误判交付完成
3. desktop / IM / cross-channel 状态语义统一

## 阶段 D 验收

1. 复杂任务 token / latency 有可见下降
2. memory fallback 风险显著降低
3. 启动日志更聚焦关键问题

---

## 8. 回退计划

## 8.1 原则

每个任务包必须独立可回退，禁止多项高风险改动捆绑上线。

## 8.2 回退方式

建议按任务包单独提交或单独 feature flag 控制：

1. 任务包 0 不应与功能改动混提
2. A1 可独立回退
3. A2 可独立回退
4. B1/B2 最好分两个提交
5. D1/D2 必须带开关

## 8.3 不建议的发布方式

不建议：

1. 将 A/B/C/D 四个阶段一次性合并上线
2. 在没有真实回归时直接启用 strict tenant 模式
3. 在没有缓存隔离测试时直接开启子代理缓存
4. 在未确认 desktop 前端契约前，直接假设 backend 兼容修复已经覆盖全部附件显示问题
5. 将已收口的 Todo UI / 可见化问题与本轮协议修复捆绑重做

---

## 9. 建议提交策略

建议按“一个逻辑改动一个提交”拆分：

1. `im_channel: normalize legacy deliver_artifacts file_path into artifacts manifest`
2. `browser: auto-create screenshot parent directories before saving`
3. `todo_handler: accept legacy step_1 ids when updating planner steps`
4. `reasoning/response: distinguish local_only from delivered artifact states`
5. `feishu: expose cardkit degradation as capability instead of repeating full startup noise`
6. `prompt/agent: cache stable prompt segments for sub-agent runs`
7. `memory: disable long-term writes when tenant falls back to default`

这样便于：

1. 单独验证
2. 单独回退
3. 审查时聚焦

---

## 10. 推荐跟踪方式

建议在后续开发跟进中，为每个任务包记录：

1. 负责人
2. 当前状态
3. 关联 PR / commit
4. 风险等级
5. 单测状态
6. 真实验证状态
7. 是否可回退

推荐维护一份单独的进度文件，例如：

- `docs/callchain-reliability-progress.md`

如后续需要，可以在本计划基础上继续生成该进度文档模板。

---

## 11. 文档关系

本文件负责回答：

1. 如何实施
2. 如何测试
3. 如何分阶段推进
4. 如何验收和回退

设计背景与方案取舍请参见：

- `docs/callchain-reliability-design.md`
