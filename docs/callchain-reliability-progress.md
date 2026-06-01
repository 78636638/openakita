# 调用链可靠性与兼容性改造进度跟踪

## 1. 文档说明

本文件用于跟踪 `docs/callchain-reliability-design.md` 与 `docs/callchain-reliability-development-plan.md` 的实际执行进度。

建议使用方式：

1. 每完成一个任务包，更新一次状态
2. 每提交一个 PR / commit，补充关联信息
3. 每轮真实验证后，补充验证结果
4. 出现阻塞或风险变化时，优先更新本文件

本文件重点回答：

1. 当前做到哪一步
2. 哪些任务已经完成
3. 哪些任务还在阻塞
4. 哪些风险仍未关闭
5. 每个任务是否具备回退条件

---

## 2. 当前总体状态

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| 设计文档 | 已完成 | `docs/callchain-reliability-design.md` |
| 实施计划 | 已完成 | `docs/callchain-reliability-development-plan.md` |
| 基线契约核对 | 已完成 | 阶段 0 已执行并冻结当前基线 |
| 阶段 A 交付可靠性修复 | 可开始 | 阶段 0 已确认问题边界 |
| 阶段 B Todo 协议统一 | 未开始 | 待执行 |
| 阶段 C 交付状态与通道能力治理 | 未开始 | 待执行 |
| 阶段 D 性能与租户治理 | 未开始 | 待执行 |

---

## 3. 里程碑状态

| 里程碑 | 状态 | 目标 | 完成标准 |
| --- | --- | --- | --- |
| M0 基线冻结 | 已完成 | 确认前后端契约和本轮不改范围 | 已完成阶段 0 契约核对 |
| M1 交付可靠 | 可开始 | 修复 legacy 交付与截图自愈 | `missing_artifacts` 不再由 legacy 参数触发 |
| M2 Todo 一致 | 未开始 | 统一 planner 场景下的步骤协议 | `update_todo_step` 不再 miss |
| M3 状态可观测 | 未开始 | 区分 local_only / delivered / failed | UI 与验证语义一致 |
| M4 性能与隔离 | 未开始 | 控制 token 与 tenant 风险 | 性能下降趋势明确、tenant fallback 降低 |

---

## 4. 阶段进度

## 阶段 0：契约核对与基线冻结

### 状态

- 当前状态：已完成
- 负责人：AI Agent
- 执行时间：已完成一次静态代码 + 历史真实日志核对
- 结论：当前系统基线已冻结，可进入阶段 A

### 核心任务

- [x] 核对 desktop 前端是否已消费 `file_url` / receipts
- [x] 核对 `deliver_artifacts` 在 desktop / IM / cross-channel 下的返回结构
- [x] 核对非 planner `todo_required` 可见 Todo 链路是否稳定
- [x] 核对 planner 场景 Todo 独立面板是否稳定
- [x] 冻结本轮明确不改范围

### 真实验证

- [x] Desktop 附件契约代码核对
- [x] 非 planner Todo 真实验证复用既有日志样本
- [x] Planner Todo 真实验证复用既有日志样本

### 输出物

- [x] 契约核对结论
- [x] 当前基线样本日志清单

### 风险与阻塞

- 当前风险：已确认 backend legacy 参数兼容缺口与 planner step id 协议错位，但边界清晰
- 当前阻塞：无

### 阶段 0 结论

#### 1. Desktop 前端契约已成立

当前 desktop 前端已经消费后端的 `artifact.file_url`，并会把相对路径拼接为可展示/可下载 URL：

1. `apps/setup-center/src/views/chat/components/Artifacts.tsx` 会读取 `art.file_url`
2. `apps/setup-center/src/views/ChatView.tsx` 会接收 SSE `artifact` 事件并保存 `file_url`
3. `apps/setup-center/src/types.ts` 与 `chatTypes.ts` 已把 `file_url` 定义为既有契约字段

冻结结论：

1. 当前附件显示问题不能笼统归因于 frontend 未消费
2. 阶段 A 应优先修 backend 顶层 legacy `file_path/path` 到 `artifacts` 的归一化缺口
3. receipts / artifact 结构改造必须“只追加字段，不重命名旧字段”

#### 2. Backend desktop 交付契约已具备 `file_url`

`src/openakita/tools/handlers/im_channel.py` 的 `_deliver_artifacts_desktop()` 当前已经：

1. 将文件映射为 `/api/files?path=...`
2. 在 receipts 中返回 `file_url`
3. 明确提示 frontend 应基于 `file_url` 做 inline display

冻结结论：

1. desktop 主协议本身是成立的
2. 当前真正的问题边界是 `_normalize_delivery_params()` 未吸收顶层 `file_path/path`
3. 阶段 A1 不应重写 desktop 交付协议，只应修 legacy 参数兼容

#### 3. 非 planner `todo_required` 路径当前基线稳定

既有真实日志已证明：非 planner 的 `todo_required=True` 任务会先显示 bootstrap Todo，再进入执行。

冻结结论：

1. 非 planner 可见 Todo 已视为当前稳定基线
2. 阶段 B 不应重复改动 bootstrap `step_1/step_2/step_3` 生成逻辑
3. 阶段 B 只聚焦 planner 场景下的步骤协议错位

#### 4. Planner 路径当前仍存在步骤协议错位

日志显示 planner 生成的步骤为 `1/2/3/4`，但运行时更新使用 `step_1/step_2`，导致：

1. `update_todo_step` 找不到步骤
2. planner Todo 状态与真实执行不同步

冻结结论：

1. 阶段 B1 先做读兼容
2. 阶段 B2 再做写规范化
3. 不应把此问题扩大成整个 Todo 系统重做

#### 5. 本轮明确不改范围

以下能力在阶段 0 已明确冻结，不纳入本轮主改造：

1. 非 planner 可见 Todo bootstrap 链路
2. CLI Todo 独立面板布局
3. Feishu / IM 启动链幂等保护
4. 服务重启窗口 `Event loop is closed` 修复
5. 浏览器一致性提示误报修复

### 基线证据

#### 代码契约证据

1. `apps/setup-center/src/views/chat/components/Artifacts.tsx`
2. `apps/setup-center/src/views/ChatView.tsx`
3. `apps/setup-center/src/views/chat/utils/chatTypes.ts`
4. `apps/setup-center/src/types.ts`
5. `src/openakita/tools/handlers/im_channel.py`

#### 日志样本证据

1. 非 planner Todo 可见化：`logs/cli_todo_visibility_verify_2_20260531.clean.log`
2. planner Todo + browser 编排：`logs/cli_taskplanner_browser_verify_20260531.clean.log`
3. planner step id 错位与 `missing_artifacts`：`logs/openakita.log`

---

## 阶段 A：交付可靠性修复

### 状态

- 当前状态：进行中
- 负责人：AI Agent
- 计划开始时间：已启动
- 计划完成时间：待定

### 任务包 A1：`deliver_artifacts` legacy 参数兼容

| 项目 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 负责人 | AI Agent |
| 修改文件 | `src/openakita/tools/handlers/im_channel.py` |
| 风险等级 | 低 |
| 可回退 | 是 |
| PR / Commit | 待补充 |

检查项：

- [x] 顶层 `file_path/path` 自动归一化为 `artifacts`
- [x] receipts 结构仅追加字段，不重命名
- [x] desktop 模式单测通过
- [x] IM 模式单测通过
- [x] 真实验证通过

### 任务包 A2：截图与文件输出目录自愈

| 项目 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 负责人 | AI Agent |
| 修改文件 | `src/openakita/tools/handlers/browser.py`, `src/openakita/tools/browser/playwright_tools.py` |
| 风险等级 | 低 |
| 可回退 | 是 |
| PR / Commit | 待补充 |

检查项：

- [x] screenshot 自动创建父目录
- [x] 非法路径错误码稳定
- [x] 真实浏览器任务无额外 `mkdir -p`

### 任务包 A3：交付状态结构化

| 项目 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 负责人 | AI Agent |
| 修改文件 | `src/openakita/tools/handlers/im_channel.py`, `src/openakita/core/response_handler.py`, `tests/unit/test_im_channel_handler.py`, `tests/unit/test_response_handler.py` |
| 风险等级 | 中低 |
| 可回退 | 是 |
| PR / Commit | 待补充 |

检查项：

- [x] `delivery_state` 字段定义明确
- [x] `local_only / delivered / failed` 可区分
- [x] `ResponseHandler` 摘要补充 `delivery_state` 计数
- [x] 定向单测通过

---

## 阶段 B：Todo 协议统一

### 状态

- 当前状态：未开始
- 负责人：待定
- 计划开始时间：待定
- 计划完成时间：待定

### 任务包 B1：Todo Step ID 读兼容

| 项目 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 负责人 | AI Agent |
| 修改文件 | `src/openakita/tools/handlers/todo_handler.py`, `src/openakita/cli/stream_renderer.py`, `tests/unit/test_todo_step_id_compat.py` |
| 风险等级 | 低 |
| 可回退 | 是 |
| PR / Commit | 待补充 |

检查项：

- [x] `1` / `step_1` 双兼容
- [x] 旧 plan 恢复不回归
- [x] 定向回归测试通过

### 任务包 B2：Todo Step ID 写规范化

| 项目 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 负责人 | AI Agent |
| 修改文件 | `src/openakita/core/task_planner.py`, `src/openakita/core/agent.py`, `tests/unit/test_task_planner.py` |
| 风险等级 | 中 |
| 可回退 | 是 |
| PR / Commit | 待补充 |

检查项：

- [x] 新链路只写 canonical id
- [x] 非 planner bootstrap 不受影响
- [x] planner / UI / archived turn 状态一致

---

## 阶段 C：交付状态与通道能力治理

### 状态

- 当前状态：未开始
- 负责人：待定
- 计划开始时间：待定
- 计划完成时间：待定

### 任务包 C1：Feishu 能力分层与降噪

| 项目 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 负责人 | AI Agent |
| 修改文件 | `src/openakita/channels/adapters/feishu.py`, `src/openakita/tools/handlers/im_channel.py`, `tests/unit/channels/test_feishu_lifecycle.py`, `tests/unit/test_im_channel_handler.py` |
| 风险等级 | 低 |
| 可回退 | 是 |
| PR / Commit | 待补充 |

检查项：

- [x] 基础消息能力与 CardKit 能力拆分
- [x] 启动日志改为聚合降级摘要
- [x] 基础文本/文件发送回归通过
- [x] Feishu 真机发送验证通过

### 任务包 C2：响应验证与前端展示联动

| 项目 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 负责人 | AI Agent |
| 修改文件 | `src/openakita/core/response_handler.py`, `src/openakita/core/validators.py`, `tests/unit/test_response_handler.py`, `tests/unit/test_org_delegation_validator.py` |
| 风险等级 | 中 |
| 可回退 | 是 |
| PR / Commit | 待补充 |

检查项：

- [x] `local_only` 与 `delivered` verify 语义区分
- [x] 外部送达请求不再把 `local_only` 误判为已送达
- [x] 本地产物场景保留 deterministic pass

---

## 阶段 D：性能与租户治理

### 状态

- 当前状态：未开始
- 负责人：待定
- 计划开始时间：待定
- 计划完成时间：待定

### 任务包 D1：性能缓存第一步

| 项目 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 负责人 | AI Agent |
| 修改文件 | `src/openakita/core/agent.py`, `src/openakita/core/feature_flags.py`, `tests/unit/test_prompt_cache_isolation.py` |
| 风险等级 | 中 |
| 可回退 | 需开关 |
| PR / Commit | 待补充 |

检查项：

- [x] prompt cache key 补齐 session/workspace/authorized-intent 维度
- [x] tool_filter 不串缓存
- [x] 多 session 不串缓存
- [x] 性能对比样本已记录

性能样本记录：

- 样本方法：复用 `tests/unit/test_prompt_cache_isolation.py` 中的 fake `Agent / prompt_assembler` 结构，避免真实 LLM、catalog 与网络 I/O 噪音，只测 prompt cache key 构造与缓存命中/隔离行为。
- 对比开关：分别测 `prompt_cache_isolation_v1 = False` 与 `prompt_cache_isolation_v1 = True`。
- 样本场景：`same_session_hot_hit`、`cross_session_alternating`、`tool_filter_alternating`。
- 采样方式：每组运行 7 次，每次循环 2000 次，记录 `median_ms / min_ms / max_ms / assembler_calls`。

| 场景 | flag_off | flag_on | 结论 |
| --- | --- | --- | --- |
| same_session_hot_hit | `median 35.709ms`，`assembler_calls=[1 x7]` | `median 46.298ms`，`assembler_calls=[1 x7]` | 开启隔离后热命中仍只组装 1 次，额外成本主要来自更完整的 cache key 构造 |
| cross_session_alternating | `median 30.493ms`，`assembler_calls=[1 x7]` | `median 41.650ms`，`assembler_calls=[2 x7]` | 关闭隔离时跨 session 错误共享缓存；开启后按预期拆成两个缓存项 |
| tool_filter_alternating | `median 29.783ms`，`assembler_calls=[1 x7]` | `median 41.295ms`，`assembler_calls=[2 x7]` | 关闭隔离时不同 `required_tools` 错误复用；开启后 tool filter 正确隔离 |

样本结论：

1. `prompt_cache_isolation_v1` 没有破坏同 session 热命中，assembler 调用次数仍稳定为 1。
2. 开启隔离后，跨 session 与跨 `required_tools` 的交替请求会从“错误共享 1 个缓存项”变为“正确拆分 2 个缓存项”，这是预期的纠偏成本。
3. 在当前轻量样本下，隔离键带来的额外耗时约为 `10ms~12ms / 2000 次调用`，量级可接受，且换来缓存污染风险收敛。

### 任务包 D2：tenant 透传与 memory 安全

| 项目 | 内容 |
| --- | --- |
| 状态 | 已完成 |
| 负责人 | AI Agent |
| 修改文件 | `src/openakita/memory/manager.py`, `src/openakita/memory/storage.py`, `src/openakita/memory/unified_store.py`, `src/openakita/core/agent.py`, `src/openakita/tools/handlers/memory.py`, `src/openakita/core/feature_flags.py`, `tests/unit/test_memory_tenant_guard.py` |
| 风险等级 | 中高 |
| 可回退 | 需开关 |
| PR / Commit | 待补充 |

检查项：

- [x] tenant 来源日志补充
- [x] fallback tenant 时禁长期写入
- [x] 本地 CLI / desktop 不受破坏
- [x] API / IM 提供真实 tenant 时正常

---

## 5. 风险清单

| 风险 | 等级 | 当前状态 | 应对策略 |
| --- | --- | --- | --- |
| backend 修了 legacy 参数，但 frontend 仍未消费 `file_url` | 高 | 已核对关闭 | 阶段 0 已确认 frontend 现有契约成立 |
| Todo step id 写规范化导致旧 plan 恢复异常 | 中 | 未关闭 | 先做 B1 读兼容，再做 B2 |
| 交付状态新字段影响旧前端 | 中 | 未关闭 | 只追加字段，不重命名旧字段 |
| prompt/cache 隔离不完整导致串缓存 | 中高 | 未关闭 | D1 必须带开关和隔离测试 |
| tenant 严格策略破坏旧入口 | 中高 | 未关闭 | 分阶段 warn -> disable write -> strict |

---

## 6. 测试与验证状态

## 6.1 单元测试

| 测试项 | 状态 | 说明 |
| --- | --- | --- |
| deliver_artifacts legacy 参数兼容 | 已完成 | `tests/unit/test_im_channel_handler.py -q` |
| screenshot auto mkdir | 已完成 | `tests/unit/test_browser_handler.py -q` |
| delivery_state 结构化 | 已完成 | `tests/unit/test_im_channel_handler.py tests/unit/test_response_handler.py -q` |
| Todo step id 双兼容 | 已完成 | `tests/unit/test_todo_step_id_compat.py tests/unit/test_stream_renderer_todo.py tests/unit/test_task_planner.py -q` |
| Todo canonical id 全链路 | 已完成 | `tests/unit/test_task_planner.py tests/unit/test_todo_step_id_compat.py tests/unit/test_stream_renderer_todo.py -q` |
| response verify 状态区分 | 已完成 | `tests/unit/test_response_handler.py tests/unit/test_org_delegation_validator.py -q` |
| Feishu 能力降级 | 已完成 | `tests/unit/channels/test_feishu_lifecycle.py tests/unit/test_im_channel_handler.py -q` |
| prompt cache 隔离 | 已完成 | `tests/unit/test_prompt_cache_isolation.py tests/unit/test_propagate_skill_change.py tests/unit/test_config_handler_select_endpoint.py -q` |
| tenant fallback 安全策略 | 已完成 | `tests/unit/test_memory_tenant_guard.py tests/unit/test_memory_owner_isolation.py tests/unit/test_memory_tool_input_resilience.py tests/unit/test_memory_v4_migration_and_isolation.py -q` |

## 6.2 真实验证

| 场景 | 状态 | 说明 |
| --- | --- | --- |
| desktop 附件展示 | 已完成契约核对 | frontend 已消费 `file_url` |
| 非 planner Todo 可见化 | 已复用样本确认 | `cli_todo_visibility_verify_2_20260531.clean.log` |
| planner Todo + browser 编排 | 已复用样本确认 | `cli_taskplanner_browser_verify_20260531.clean.log` |
| prompt cache 隔离性能样本 | 已记录基准样本 | 轻量 fake agent benchmark；热命中仍为单次组装，跨 session / tool filter 改为正确分桶 |
| browser -> screenshot -> deliver_artifacts | 已完成真机压测 | Headless 浏览器访问 `example.com`，截图输出到多级新目录后通过 legacy `file_path` + cross-channel 成功发往 Feishu，连续 3 轮均 `delivered=1` |
| Feishu 基础能力未退化 | 已完成真机压测 | `adapter.send_text` 与会话内 `deliver_artifacts(file_path=...)` 连续 3 轮成功；CardKit 探测降级为 `patch_message`，但文本/文件交付保持可用 |
| delivery_state 语义对比 | 已完成真机对照 | 同一批真实产物在 Feishu 会话内为 `delivered=1`，Desktop 模式返回 `local_only=1`，符合 C2 分层语义 |
| browser_screenshot 非法路径错误稳定性 | 已完成真机压测 | 真实浏览器页面下对 3 类非法路径各执行 3 次：文件父路径稳定返回 `Errno 17`，`/proc` 缺失目录稳定返回 `Errno 2`，`/sys/kernel` 受限路径稳定返回 `Errno 1` |
| LLM 端到端浏览器任务 | 已完成真机压测 | `openakita --auto-confirm run` 真实调用 `browser_navigate` + `browser_screenshot`，截图保存到 `data/temp/llm_e2e_browser/example_cli.png`，任务 `21.2s / 4 iterations / success=True` |
| LLM 端到端 Feishu 交付任务 | 已完成真机压测 | 启动核心服务与 IM 通道后，LLM 真实调用 `write_file` + `deliver_artifacts(target_channel=feishu)`；成功生成 `report_20260531_030633.txt` 并投递到 Feishu，回执 `1/1 delivered` |

---

## 7. 变更记录

按时间追加：

| 日期 | 变更 | 责任人 | 备注 |
| --- | --- | --- | --- |
| 2026-05-31 | 完成 A2 非法路径专项真机测试：`browser_screenshot` 对 3 类非法路径连续 3 次稳定返回相同 `Errno`，错误语义未漂移 | AI Agent | `Errno 17 / 2 / 1` 分别对应文件父路径、`/proc` 缺失目录、`/sys/kernel` 权限受限 |
| 2026-05-31 | 完成第 3 轮 LLM 端到端压测：CLI 真实浏览器任务成功调用 `browser_navigate` 和 `browser_screenshot`，Feishu 真实交付任务成功调用 `write_file` 和 `deliver_artifacts(target_channel=feishu)` | AI Agent | 浏览器任务 `21.2s/4 iterations`；Feishu 任务 `11.1s/3 iterations` |
| 2026-05-31 | 完成第 2 轮真机压测：真实浏览器截图链路连续 3 轮成功投递到 Feishu，且 Feishu 会话内文本/文件发送与 `delivery_state` 分层表现符合预期 | AI Agent | 浏览器组 3/3 成功，Feishu 组文本基线 + 文件 3/3 成功 |
| 2026-05-31 | 补充 D1：记录 prompt cache 隔离的性能对比样本，确认热命中未回归、跨 session / tool filter 由错误共享变为正确隔离 | AI Agent | 微基准 3 组场景，各运行 7 次 |
| 2026-05-31 | 完成 D2：为 memory 增加 tenant 安全 guard，远端 fallback 不再登记 session_tenants 且禁止长期写入，并提供 feature flag 回退 | AI Agent | 定向测试 `65 passed` |
| 2026-05-31 | 完成 D1：为 system prompt cache 增加 session/workspace/tool-filter/authorized-intent 隔离维度，并提供 feature flag 回退 | AI Agent | 定向测试 `28 passed` |
| 2026-05-31 | 完成 C1：聚合 Feishu CardKit 能力降级摘要，并把降级交付提示透传到 IM 附件回执 payload | AI Agent | 定向测试 `14 passed` |
| 2026-05-31 | 完成 C2：让 response verify 区分 `delivered / local_only / failed`，并收紧外部送达语义 | AI Agent | 定向测试 `59 passed` |
| 2026-05-31 | 完成 B2：将 planner 输出与 agent 写入链路的 Todo step id 统一为 `step_N` canonical 形式 | AI Agent | 定向测试 `74 passed` |
| 2026-05-31 | 完成 B1：为 Todo step id 增加 `1` / `step_1` 双向读兼容，覆盖工具处理器与 CLI 渲染器 | AI Agent | 定向测试 `74 passed` |
| 2026-05-31 | 完成 A3：为 `deliver_artifacts` 的 desktop / IM / cross-channel 回执增加 `delivery_state`，并让 `ResponseHandler` 汇总 `local_only / delivered / failed` 计数 | AI Agent | 定向测试 `54 passed` |
| 待补充 | 初始化进度模板 | 待补充 | 当前仅建立跟踪骨架 |
| 2026-05-30 | 完成阶段 0 契约核对与基线冻结 | AI Agent | 已确认 frontend `file_url` 契约、非 planner / planner Todo 双路径基线与本轮不改范围 |

---

## 8. 待决事项

| 事项 | 当前结论 | 责任人 | 截止时间 |
| --- | --- | --- | --- |
| desktop 前端是否已稳定消费 `file_url` | 待确认 | 待定 | 待定 |
| receipts 新字段是否需要同步前端类型定义 | 待确认 | 待定 | 待定 |
| tenant strict 模式是否默认关闭 | 建议默认关闭 | 待定 | 待定 |
| 启动 warning 聚合是否与阶段 D 同批上线 | 建议放阶段 D | 待定 | 待定 |

---

## 9. 使用说明

推荐后续推进时按以下方式维护本文件：

1. 开始一个任务包时，将状态从“未开始”改为“进行中”
2. 提交 PR 后补充 `PR / Commit`
3. 跑完测试后更新“单元测试 / 真实验证”表
4. 每次周会或阶段评审前，至少更新一次“风险清单”和“待决事项”
5. 若出现范围变化，先更新设计文档和实施计划，再更新本文件

---

## 10. 关联文档

- 设计文档：`docs/callchain-reliability-design.md`
- 实施计划：`docs/callchain-reliability-development-plan.md`
