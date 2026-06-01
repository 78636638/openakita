# 调用链可靠性与兼容性改造设计方案

## 1. 背景

基于 `logs/openakita.log` 的真实运行日志分析，当前 OpenAkita 主链路已经具备完整能力：

1. 接收用户请求
2. 进行意图分析
3. 判断是否需要 Todo 与 TaskPlanner
4. 按需拆解任务并委派子代理
5. 进入 ReAct 推理与工具执行
6. 生成最终结果
7. 尝试通过桌面前端或 IM 通道交付附件/结果

从系统能力角度看，这条链路是可工作的；但从“稳定性、可解释性、交付可靠性、兼容性、性能和多租户安全”角度看，仍存在一批已经在真实日志中暴露的问题。

这些问题并不适合通过一次性重构解决。更合理的方式是：

- 保持主链路结构不变
- 对关键薄弱点做低破坏、可灰度、可回退的增量治理
- 让系统先从“能跑”提升到“稳定、可观测、可持续演进”

本设计文档用于指导后续改造实施。

---

## 1.1 当前已完成的相关收口

在进入本轮“调用链可靠性与兼容性”设计之前，系统已有一批相关问题完成收口，这些能力应视为当前基线，而不是重新设计对象：

1. `todo_required` 任务已统一为“先出现可见 Todo，再进入执行”
2. CLI 中 Todo 已有独立面板和更合理的显示位置
3. 浏览器任务“一致性提示误报”已完成修复
4. 报告类结果在 Turn 详情中的完整显示问题已完成修复
5. Feishu / IM 启动链幂等保护已完成修复
6. 服务重启窗口中的 `Event loop is closed` 竞态已完成修复

因此，本设计不应重新打开以下已稳定方向：

1. 不回退当前“先显示 Todo 再执行”的交互方向
2. 不回退当前 Todo 独立面板的 UI 结构
3. 不重做已完成的 Feishu / restart loop 收口
4. 不重新讨论“所有 Todo 是否都强制接 TaskPlanner”

---

## 2. 设计目标

### 2.1 核心目标

本次改造聚焦以下 4 个目标：

1. 提升附件交付与结果回显的可靠性
2. 提升 Todo / TaskPlanner / UI 三者之间的一致性
3. 降低复杂任务在多代理场景下的额外性能开销
4. 降低服务模式下的租户隔离风险

### 2.2 约束目标

所有改造都必须满足以下约束：

1. 不推倒现有 `Agent -> IntentAnalyzer -> TaskPlanner -> Orchestrator -> ReasoningEngine -> ToolExecutor` 主骨架
2. 不破坏现有 CLI、Desktop、IM 通道基础能力
3. 对旧 prompt、旧 skill、旧 trace、旧 plan 文件尽量保持兼容
4. 每项改造都应具备：
   - 明确影响范围
   - 明确回归测试
   - 明确回退策略

### 2.3 非目标

本设计不包含以下内容：

1. 不重写 ReAct 引擎
2. 不将所有 Todo 任务一刀切改成 TaskPlanner 任务
3. 不替换现有内存系统或多代理框架
4. 不重构全部通道适配器
5. 不在本阶段重写日志系统

---

## 3. 当前调用链路概览

## 3.1 主链路

当前典型链路如下：

1. 用户请求进入 `Agent.chat_with_session()` 或流式入口
2. `IntentAnalyzer` 使用编译器模型解释任务类型、复杂度、工具倾向
3. Agent 根据 `todo_required`、多步启发式、外部动作意图决定是否开启 Todo 模式
4. 对适合编排的任务，进入 `TaskPlanner`
5. `TaskPlanner` 生成子任务并初始化 Todo
6. `Orchestrator` 将子任务按 `tool_filter` 委派给子代理
7. 子代理进入 `ReasoningEngine` 的 ReAct 循环
8. `ToolExecutor` 执行工具
9. `ReasoningEngine` 汇总观察结果并生成 `FINAL_ANSWER`
10. 如果存在附件交付，调用 `deliver_artifacts`
11. 最终结果回显到 CLI / Desktop / IM

## 3.2 相关代码锚点

关键实现位置如下：

- Todo 判定: `src/openakita/core/agent.py`
- Planner 触发: `src/openakita/core/agent.py`
- TaskPlanner 执行: `src/openakita/core/agent.py`
- 子任务委派: `src/openakita/agents/orchestrator.py`
- ReAct 推理: `src/openakita/core/reasoning_engine.py`
- Todo 状态持久化: `src/openakita/tools/handlers/todo_handler.py`
- 附件交付: `src/openakita/tools/handlers/im_channel.py`
- 响应验证: `src/openakita/core/response_handler.py`

---

## 4. 现状问题总览

基于日志和当前代码结构，问题可分为 4 组。

## 4.1 交付可靠性问题

表现：

1. `deliver_artifacts` 在 desktop 模式下出现 `missing_artifacts`
2. 工具执行已经产出文件，但前端没有收到可展示的附件
3. 用户看到“任务完成”，但实际只完成了“本地保存”，未完成“真正交付”

根因：

1. `deliver_artifacts` 的正式协议是 `artifacts=[...]`
2. 当前实现已经能兼容嵌套在 artifact item 内部的 `file_path`，但对顶层 legacy 形态仍不覆盖
3. 现实中仍存在传顶层 `file_path/path` 的调用
4. desktop 模式虽然有 `_normalize_delivery_params()`，但没有覆盖所有旧参数入口

风险：

1. 用户误判结果已交付
2. 前端看不到产物
3. 验证链路混乱，容易出现“执行成功、展示失败”

## 4.2 Todo 协议一致性问题

表现：

1. Plan 中步骤 ID 是 `"1"`, `"2"` 这类数字 ID
2. 运行时更新使用 `step_1`, `step_2`
3. 导致 `update_todo_step` 查找失败

风险：

1. UI 中 Todo 进度不可信
2. Planner 执行已推进，但前端仍显示卡住
3. 归档记录与真实执行产生偏差

## 4.3 工具层自愈能力不足

表现：

1. `browser_screenshot` 写入目录不存在时直接失败
2. 模型必须额外调用 `run_shell mkdir -p` 自行修复

风险：

1. 增加额外 LLM 轮次和工具调用
2. 降低复杂任务稳定性
3. 将工具层职责错误地下沉给模型

## 4.4 性能与治理问题

表现：

1. 子代理初始化 prompt 偏大
2. 工具目录、policy、budget 存在重复构建
3. 部分 session 以 `default/default` 进入长期记忆
4. Feishu CardKit 权限缺失造成能力降级，但日志噪音很高

风险：

1. 复杂任务 token 成本高
2. 延迟增加
3. 多租户场景存在 memory 污染风险
4. 真实问题被启动 warning 淹没

## 4.5 当前文档需要明确排除的误区

为了避免后续实施跑偏，需要特别排除以下误区：

1. `deliver_artifacts` 当前暴露的是“顶层 legacy 参数兼容缺口”，不是整个交付系统失效
2. `Todo step_id` 问题主要发生在 `TaskPlanner -> 子任务更新` 这条链路，不应与已经完成的“非 planner 可见 Todo bootstrap”混为一谈
3. `default/default` memory fallback 的高风险场景主要是服务模式、多租户 API、IM 入口；对单用户本地 CLI 的影响相对有限
4. Feishu CardKit 权限缺失是能力降级问题，不等于 Feishu 基础文本/文件发送能力失效
5. 启动 warning 聚合是运维体验优化，不应排在功能正确性修复之前

---

## 5. 设计原则

## 5.1 增量改造优先

优先做“兼容层、自愈层、观测层”改造，不优先做骨架重构。

原因：

1. 当前主链路整体可用
2. 真正的问题集中在协议边界、工具契约和可观测性
3. 增量修复更容易验证、更容易回退

## 5.2 兼容读取，规范写入

对已有协议采取：

1. 读取时尽可能兼容旧形态
2. 新写入统一到新规范
3. 经一段过渡期后再考虑彻底收口

这适用于：

1. `deliver_artifacts` legacy 参数
2. Todo `step_id`
3. 旧 plan 文件恢复

## 5.3 工具问题由工具层承担

凡是“目录不存在”“输出路径需要准备”“附件参数可自动归一化”这类问题，应由工具层处理，不应让模型额外推理和补救。

## 5.4 可观测性优先于猜测

需要明确区分：

1. 通道已启动
2. 工具已调用
3. 文件已生成
4. 附件已送达
5. 前端已可展示

只有这样，系统日志、验证逻辑和用户体验才能一致。

## 5.5 服务模式下优先安全隔离

凡涉及 memory / session / tenant 的问题，优先保守处理：

1. 先阻止高风险写入
2. 再逐步补齐真实 tenant 透传
3. 不直接硬拦截全部旧入口

## 5.6 优先修边界契约，不重复改已稳定骨架

本轮优先修的是边界契约问题，而不是骨架问题：

1. 修参数归一化，而不是重写 `deliver_artifacts`
2. 修步骤 ID 协议，而不是重写 Todo 系统
3. 修工具自愈，而不是让模型继续补 shell
4. 修状态可观测性，而不是改写 TaskPlanner 的总体定位

---

## 5.7 新字段尽量只追加，不重命名

凡涉及 receipts、delivery state、Todo plan 状态等结构化字段，优先采用“追加字段”的兼容方式：

1. 不随意删除已有字段
2. 不随意重命名已有字段
3. 不改变已有成功路径的核心语义

这样可以最大限度降低对：

1. 旧前端
2. 旧验证逻辑
3. 已有 trace / log 解析
4. 现有 tool handlers（如 sticker / browser / IM）  

的破坏性。

---

## 6. 方案总览

本次改造建议拆成 6 个方案包。

## 方案 A：附件交付兼容层

### 目标

让旧形态 `deliver_artifacts` 调用在 desktop / IM / cross-channel 下都尽可能成功归一化。

### 方案内容

在 `src/openakita/tools/handlers/im_channel.py` 中增强 `_normalize_delivery_params()`：

1. 当 `artifacts` 为空时，吸收以下顶层字段：
   - `file_path`
   - `path`
   - `caption`
   - `name`
   - `type`
2. 自动包装成标准 `artifacts` 列表
3. 如果只有单个文件路径，则自动构建单元素 artifact manifest
4. 根据文件后缀自动补全 `type`

### 范围边界

这里建议修的是“顶层 legacy 形态”的归一化，不建议修改以下内容：

1. 不重写 `artifacts` 正式协议
2. 不重命名现有 receipts 结构
3. 不修改 desktop `/api/files` 交付模式
4. 不把 frontend 展示逻辑塞回 backend 推理链路

### 推荐行为

输入：

```json
{
  "file_path": "/tmp/a.png",
  "caption": "截图"
}
```

归一化后：

```json
{
  "artifacts": [
    {
      "type": "image",
      "path": "/tmp/a.png",
      "caption": "截图"
    }
  ]
}
```

### 兼容性

1. 对正式 `artifacts` 调用完全无影响
2. 对 legacy prompt / old skill / old trace 立即生效
3. 属于向后兼容增强
4. 对已经使用嵌套 artifact item 的调用不重复处理

### 破坏性

极低。

### 回退方式

如果出现异常，仅需回退 `_normalize_delivery_params()` 中的兼容包装逻辑。

## 方案 B：Todo Step ID 统一协议

### 目标

解决 `1` 与 `step_1` 并存导致的更新失败。

### 方案内容

1. 确立 Todo canonical step id 为 TaskPlanner 的原始 `task.id`
2. 在 `todo_handler` 中增加步骤 ID 归一化函数
3. 对以下输入全部兼容：
   - `1`
   - `step_1`
   - `Step_1`
4. 所有新事件、新持久化、新回写统一使用 canonical id

### 分阶段策略

阶段 1：

1. 读兼容
2. 查找兼容
3. 写入仍保持现状

阶段 2：

1. 写规范化
2. UI 事件统一输出 canonical id

### 范围边界

本方案只处理 `TaskPlanner` 场景下的步骤协议一致性，不重新改动以下已稳定能力：

1. 非 planner `todo_required` 路径的 bootstrap 可见 Todo
2. CLI 中独立 Todo 面板的位置与布局
3. “是否进入 TaskPlanner”的选择策略

### 兼容性

高。

### 破坏性

低。

### 回退方式

仅保留读兼容，取消写规范化即可。

## 方案 C：工具层路径自愈

### 目标

将“目录不存在”这类基础 IO 问题从模型层迁回工具层。

### 方案内容

对所有产出型工具统一加父目录创建能力，优先覆盖：

1. `browser_screenshot`
2. `desktop_screenshot`
3. 其他常见导出/保存工具

统一行为：

1. 写文件前执行 `parent.mkdir(parents=True, exist_ok=True)`
2. 失败时返回结构化错误
3. 错误中包含 `error_code` 与 `hint`

### 兼容性

完全兼容。

### 破坏性

极低。

### 回退方式

可以逐工具独立回退。

## 方案 D：交付状态可观测化

### 目标

让日志、验证系统和前端能区分“本地已保存”和“已成功送达”。

### 方案内容

为交付链路补充结构化状态：

1. `delivery_mode`: `desktop` / `im` / `cross_channel`
2. `delivery_state`: `delivered` / `local_only` / `frontend_pending` / `failed`
3. `receipt_count`
4. `delivered_count`
5. `error_code`

并在以下位置统一消费：

1. `im_channel`
2. `reasoning_engine`
3. `response_handler`
4. CLI / Desktop 最终结果区

### 兼容约束

交付状态改造需要满足：

1. receipts 原结构保持兼容
2. 新状态字段只追加，不覆盖旧字段
3. 先补日志和验证，再逐步接入 UI
4. 在确认 desktop 前端已消费 `file_url` 契约前，不假设前端一定已完全显示

### 兼容性

老前端忽略新增字段即可。

### 破坏性

低。

## 方案 E：性能收敛

### 目标

降低复杂任务中 Planner + 子代理 + tool-filter 场景的 token 和延迟。

### 方案内容

1. 缓存 session 级稳定 prompt 片段
2. 缓存 policy/tool catalog 的静态部分
3. 对 sub-agent 引入更轻量的 prompt 组装模式
4. 减少子代理 handler / policy / memory 重复初始化

### 注意事项

缓存必须以以下维度隔离：

1. session
2. agent profile
3. tool_filter
4. prompt_mode
5. tenant

### 兼容性

中高，但需谨慎验证缓存隔离。

### 破坏性

中低。

## 方案 F：租户隔离与能力治理

### 目标

降低 `default/default` memory fallback 和通道能力误判风险。

### 方案内容

1. 为长期记忆写入增加真实 tenant 检查
2. 在入口层统一补齐 `user_id` / `workspace_id`
3. 对 Feishu 能力分层：
   - 基础消息可用
   - CardKit 不可用
4. 将启动 warning 聚合成摘要，减少噪音

### 顺序要求

本方案必须晚于功能正确性修复推进：

1. 先修交付与 Todo 正确性
2. 再修交付状态观测
3. 最后再收敛 tenant / startup warning 治理

### 兼容性

需要分阶段推进。

### 破坏性

中等，尤其是 tenant 强约束部分。

---

## 7. 最优改造方式

综合破坏性、兼容性、收益和实施成本，推荐采用以下最优路径。

## 7.1 第一优先级：低破坏高收益

优先落地：

1. 方案 A：附件交付兼容层
2. 方案 B：Todo Step ID 读兼容
3. 方案 C：工具层路径自愈
4. 方案 D：交付状态可观测化中的日志增强

原因：

1. 用户感知最强
2. 破坏性最低
3. 几乎不动主框架
4. 能最快改善“任务做完了但用户看不到结果”的问题

## 7.2 第二优先级：协议与状态统一

第二阶段落地：

1. 方案 B：Todo Step ID 写规范化
2. 方案 D：UI/验证系统统一消费交付状态
3. Feishu 能力分层

## 7.3 第三优先级：性能与治理

最后落地：

1. 方案 E：性能收敛
2. 方案 F：tenant 透传与长期记忆治理
3. 启动 warning 聚合

原因：

1. 这部分收益大，但牵涉面更广
2. 需要在功能稳定后推进
3. 更适合灰度与阶段验收

---

## 8. 影响范围分析

## 8.1 代码影响范围

核心文件如下：

1. `src/openakita/tools/handlers/im_channel.py`
2. `src/openakita/tools/handlers/todo_handler.py`
3. `src/openakita/core/agent.py`
4. `src/openakita/core/reasoning_engine.py`
5. `src/openakita/core/response_handler.py`
6. `src/openakita/tools/handlers/browser.py`
7. `src/openakita/tools/browser/playwright_tools.py`
8. `src/openakita/agents/orchestrator.py`

## 8.2 功能影响范围

会影响以下能力：

1. Desktop 模式附件显示
2. IM 通道附件交付
3. Planner 场景下 Todo 状态更新
4. 浏览器截图类任务成功率
5. 复杂任务 token 与延迟
6. 服务模式 memory 隔离

## 8.3 用户影响范围

受益最大的用户场景：

1. 浏览器截图/报告交付
2. 多步骤 Todo 任务
3. 桌面端看附件
4. 外部动作任务

潜在受影响场景：

1. 依赖旧 `file_path` 调用的 prompt/skill
2. 依赖旧 `step_1` 格式的内部逻辑
3. 依赖 fallback memory 的非规范入口

---

## 9. 兼容性设计

## 9.1 参数兼容

要求：

1. 继续支持 `artifacts`
2. 吸收 legacy 顶层 `file_path/path`
3. 统一归一化为内部标准 manifest

## 9.2 协议兼容

要求：

1. Todo `step_id` 过渡期双兼容
2. plan 文件恢复继续支持旧格式
3. 旧 trace 和旧日志解析不失效

## 9.3 前后端兼容

要求：

1. 新增字段不影响老前端
2. Desktop 继续使用 `/api/files` 模式
3. IM 通道的 receipts 结构尽量保持稳定
4. 后端改造前需先核对 desktop 前端当前是否已消费 `file_url` / receipts 契约

## 9.4 运行兼容

要求：

1. CLI 不受影响
2. Desktop 不受影响
3. IM 基础消息能力不受影响
4. Feishu CardKit 仅做能力降级展示，不中断主流程

---

## 10. 破坏性评估

## 10.1 低破坏项

包括：

1. `deliver_artifacts` legacy 参数兼容
2. `browser_screenshot` 自动建目录
3. Todo `step_id` 读兼容
4. 结构化日志增强

特点：

1. 不改变主协议
2. 不改变外部接口语义
3. 出问题时容易单点回退

## 10.2 中等破坏项

包括：

1. Todo `step_id` 写规范化
2. 交付状态接入验证与 UI
3. Feishu 能力分层

特点：

1. 会影响状态呈现与部分判定逻辑
2. 需要补足回归测试

## 10.3 高关注项

包括：

1. tenant 强约束
2. 子代理缓存与 prompt 缩减

特点：

1. 涉及行为边界
2. 需要灰度和开关
3. 需要更长观察期

---

## 11. 观测与验收设计

## 11.1 观测指标

建议新增或收敛以下指标：

1. `deliver_artifacts_total`
2. `deliver_artifacts_success_total`
3. `deliver_artifacts_missing_manifest_total`
4. `todo_step_update_miss_total`
5. `tool_output_path_autocreate_total`
6. `session_memory_fallback_total`
7. `subagent_prompt_tokens`
8. `taskplanner_latency_ms`

## 11.2 日志要求

建议新增结构化字段：

1. `session_id`
2. `plan_id`
3. `subtask_id`
4. `delivery_mode`
5. `delivery_state`
6. `error_code`
7. `tenant_source`
8. `frontend_contract_version`（如后续确实需要前后端联动时再引入）

## 11.3 验收标准

第一阶段验收：

1. `deliver_artifacts(file_path=...)` 不再返回 `missing_artifacts`
2. Todo 更新不再出现 `未找到步骤`
3. 截图类任务无需额外 `mkdir -p`

第二阶段验收：

1. UI 能区分 `local_only` 和 `delivered`
2. Todo 步骤更新全链路统一
3. Feishu 能力页明确降级状态

第三阶段验收：

1. 复杂任务 token / 延迟明显下降
2. `default/default` 长期记忆 fallback 次数下降
3. 启动日志关键告警更聚焦

---

## 12. 回退策略

每个方案必须独立可回退。

## 12.1 方案 A 回退

仅回退 legacy 参数归一化逻辑。

## 12.2 方案 B 回退

保留读兼容，暂停写规范化。

## 12.3 方案 C 回退

逐工具回退自动建目录逻辑。

## 12.4 方案 D 回退

保留内部 receipts，不让 UI/验证系统消费新增状态字段。

## 12.5 方案 E/F 回退

通过 feature flag 关闭：

1. sub-agent prompt light mode
2. tenant strict mode
3. warning aggregation

---

## 13. 最终建议

建议后续实施时遵循以下顺序：

1. 先修交付兼容与工具自愈
2. 再修 Todo 协议一致性
3. 再补交付状态观测和 UI/验证联动
4. 最后推进性能优化和租户治理

不建议：

1. 直接大规模重构 TaskPlanner
2. 强制所有 Todo 任务进入 Planner
3. 不经过兼容期就硬切新协议
4. 在未补观测的情况下直接做性能缓存
5. 把已收口的 Todo 可见性和 CLI 面板问题重新纳入本轮主改造范围

---

## 14. 文档关系

本文件负责回答：

1. 为什么改
2. 改什么
3. 怎么改最安全
4. 风险与兼容边界是什么

后续具体开发、测试、排期、阶段跟进，请参见：

- `docs/callchain-reliability-development-plan.md`
