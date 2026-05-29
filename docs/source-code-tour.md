# OpenAkita Source Code Tour

本文是面向开发者的源码导读。

如果 `architecture.md` 解决的是“系统怎么分层”，这份文档解决的是：

- 仓库里每个大目录是干什么的
- `src/openakita/` 里哪些目录是主干，哪些是支撑模块
- 阅读不同需求时，应该先看哪几组文件
- 哪些模块看起来相似，实际职责并不相同

## 1. 仓库顶层结构

先看项目根目录的角色分工：

### `src/openakita/`

后端主代码。绝大多数运行逻辑都在这里，是核心阅读区。

### `apps/setup-center/`

桌面端/Web 端前端：

- React 18 + TypeScript + Vite
- Tauri Rust 外壳
- 负责聊天界面、配置面板、技能/插件/MCP/组织视图、日志和反馈面板

如果你关心用户界面、桌面集成、前后端交互，从这里开始。

### `identity/`

身份与行为定义输入：

- `SOUL.md`
- `AGENT.md`
- `USER.md`
- `MEMORY.md`
- `POLICIES.yaml`
- persona
- runtime 编译产物

这是 prompt pipeline 的关键输入，而不是普通文档目录。

### `skills/`

内置和外部技能目录。以 `SKILL.md` 为入口，是能力封装层。

### `plugins/`

打包插件目录。这里能直接看到项目当前已经内置了大量内容/媒体相关插件。

### `tests/`

测试体系，含：

- `unit/`
- `component/`
- `orgs/`
- `quality/`
- `e2e/`
- `perf/`
- `smoke/`
- `legacy/`

从目录数量和覆盖面来看，项目不是“只靠手测”的代码库。

### `docs/`

系统设计、使用说明、测试手册、专项方案、运维文档都在这里。

## 2. `src/openakita/` 主目录导航

下面按“重要性 + 阅读主线”排序介绍。

### `main.py`

程序入口，负责：

- CLI 命令
- 交互式运行
- 单任务运行
- 初始化 Agent
- 初始化 SessionManager、Gateway、Orchestrator
- 启动 IM 通道和服务模式

如果你不知道程序从哪开始跑，先看这里。

### `core/`

这是后端主心脏。

如果只允许你选一个目录开始看，应该选 `core/`。

重点文件：

- `agent.py`：主 Agent 实现，系统核心协调者
- `brain.py`：LLM 访问层
- `prompt_assembler.py`：系统提示词组装入口
- `ralph.py`：Ralph 循环
- `policy_v2/`：安全策略与审批治理
- `pending_approvals.py`：待审批存储
- `memory.py`：记忆相关的核心接缝
- `supervisor.py`：监督与预算相关控制
- `streaming_tool_executor.py`：流式工具执行

### `api/`

HTTP 服务层。

重点文件：

- `server.py`：FastAPI app 创建与挂载
- `routes/`：全部路由
- `schemas.py`：接口模型
- `auth.py`：Web 访问鉴权

如果你在查：

- 某个面板调了哪个接口
- 某个 API 是怎么落到 Agent 的
- 某个 SSE 事件从哪里发出

就主要看这里。

### `agents/`

多 Agent 编排层。

它不是主 Agent 本身，而是“多个 Agent 之间如何协作”的那层。

重点文件：

- `orchestrator.py`：调度入口
- `factory.py`：实例创建与池化
- `profile.py`：Agent profile 与持久化
- `presets.py`：预设 Agent
- `fallback.py`：失败后的替代路径
- `task_queue.py`：任务排队控制

### `orgs/`

组织编排系统。

这是本项目最重、也最有辨识度的子系统之一。

重点文件：

- `runtime.py`：组织运行时
- `manager.py`：组织 CRUD
- `messenger.py`：节点间消息路由
- `blackboard.py`：共享黑板
- `event_store.py`：组织事件持久化
- `command_service.py`：组织命令执行
- `heartbeat.py`：健康检查
- `node_scheduler.py`：节点调度
- `scaler.py`：扩缩容
- `inbox.py`：节点消息箱
- `reporter.py`：报告生成
- `tool_handler.py`：组织相关工具

如果你只想理解“AI 公司 / 组织树 / 节点自治”这套能力，就从这里读。

### `skills/`

技能系统实现层。

和根目录 `skills/` 不同，这里是加载器、注册表、分类、运行态。

重点文件：

- `loader.py`：扫描和加载 SKILL.md
- `parser.py`：解析 skill metadata
- `registry.py`：注册表
- `catalog.py`：对模型暴露的 skill catalog
- `runtime_registry.py`：运行态登记
- `categories.py`：分类逻辑
- `activation.py`：激活逻辑

### `plugins/`

插件宿主层。

和根目录 `plugins/` 不同，这里是插件框架本身。

重点文件：

- `manager.py`：发现、加载、卸载、权限、故障隔离
- `manifest.py`：插件清单
- `api.py`：PluginAPI/PluginBase
- `hooks.py`：生命周期 hook
- `sandbox.py`：插件错误跟踪与隔离
- `installer.py`：安装器
- `catalog.py`：插件目录聚合

### `memory/`

长期记忆和检索层。

如果你想理解：

- 记忆怎么存
- 记忆怎么查
- mode1 / mode2 是什么
- 图谱怎么做

就应该重点看这里。

### `llm/`

模型与 provider 层。

主要职责：

- endpoint 配置加载
- provider registry
- capability 判定
- failover
- runtime config 应用

这层是 `Brain` 的下游支撑。

### `tools/`

工具系统。

重点目录：

- `handlers/`：工具实现
- `definitions/`：tool schema

可以理解成“给 LLM 用的执行能力层”。

如果你关心“read_file、run_shell、config、browser、MCP tool 是怎么实现的”，这里最重要。

### `channels/`

IM 通道层。

包含：

- gateway
- adapter registry
- 各平台适配器

如果你关心 Telegram、飞书、企微、QQ、微信等对接，从这里读。

### `scheduler/`

定时任务层。

关注：

- task definition
- task execution
- 与 Agent/Org 的连接

### `prompt/`

Prompt 编译与构建层。

和 `core/prompt_assembler.py` 的关系是：

- `core/prompt_assembler.py` 是上层装配入口
- `prompt/` 是底层 prompt pipeline 细节实现

重点文件：

- `builder.py`
- `compiler.py`
- `budget.py`

### `sessions/`

会话持久化和恢复层。

桌面端/HTTP/CLI 恢复历史时会大量依赖它。

### `tracing/`

追踪与导出。

适合排查：

- Agent 决策链
- 事件记录
- span 导出

### `testing/`

内建测试框架和自动评估样例，不等同于仓库根目录的 `tests/`。

它更像“系统内部测试能力模块”。

### `workspace/`

工作区辅助，如备份等。

### `commands/`

CLI 命令注册表。

### `setup/`

引导配置、扫码 onboarding 相关逻辑。

### `mcp_servers/` 和 `mcp_server.py`

MCP server 侧相关能力。

### `orgs/` 与 `agents/` 的区别

很多人第一次看会混淆：

- `agents/`：多个 Agent 如何协作
- `orgs/`：组织如何长期运行

前者偏“会话内调度”，后者偏“持久组织运行时”。

## 3. 按问题选择阅读路径

### 3.1 想看“用户发一句话后到底发生了什么”

建议顺序：

1. `main.py`
2. `api/routes/chat.py`
3. `core/agent.py`
4. `core/prompt_assembler.py`
5. `core/brain.py`
6. `tools/handlers/`
7. `sessions/`
8. `memory/`

### 3.2 想看“多 Agent 是怎么工作的”

建议顺序：

1. `core/agent.py`
2. `agents/orchestrator.py`
3. `agents/factory.py`
4. `agents/profile.py`
5. `agents/presets.py`

### 3.3 想看“组织编排 / AI 公司”怎么实现

建议顺序：

1. `api/routes/orgs.py`
2. `orgs/runtime.py`
3. `orgs/manager.py`
4. `orgs/messenger.py`
5. `orgs/command_service.py`
6. `orgs/blackboard.py`
7. `orgs/scaler.py`
8. `orgs/node_scheduler.py`

### 3.4 想看“技能和插件有什么区别”

技能阅读：

1. `skills/loader.py`
2. `skills/parser.py`
3. 根目录 `skills/`

插件阅读：

1. `plugins/manager.py`
2. `plugins/api.py`
3. `plugins/manifest.py`
4. 根目录 `plugins/*/plugin.json`

### 3.5 想看“安全是怎么做的”

建议顺序：

1. `core/policy_v2/`
2. `api/routes/pending_approvals.py`
3. `api/routes/config.py` 中 security 相关路由
4. `core/pending_approvals.py`
5. `core/sandbox.py`
6. `core/trusted_paths.py`
7. `identity/POLICIES.yaml`

### 3.6 想看“模型、端点、故障切换”

建议顺序：

1. `core/brain.py`
2. `llm/client.py`
3. `llm/config.py`
4. `llm/runtime_config.py`
5. `api/routes/chat_models.py`
6. `api/routes/config.py`

### 3.7 想看“桌面端怎么接后端”

建议顺序：

1. `apps/setup-center/src/`
2. `apps/setup-center/src-tauri/`
3. `src/openakita/api/`
4. `src/openakita/main.py`

## 4. 高价值阅读文件清单

如果你时间有限，先读下面这 15 个文件：

1. `src/openakita/main.py`
2. `src/openakita/core/agent.py`
3. `src/openakita/core/brain.py`
4. `src/openakita/core/prompt_assembler.py`
5. `src/openakita/core/ralph.py`
6. `src/openakita/api/server.py`
7. `src/openakita/api/routes/chat.py`
8. `src/openakita/api/routes/config.py`
9. `src/openakita/agents/orchestrator.py`
10. `src/openakita/orgs/runtime.py`
11. `src/openakita/plugins/manager.py`
12. `src/openakita/skills/loader.py`
13. `src/openakita/llm/config.py`
14. `src/openakita/core/policy_v2/engine.py`
15. `src/openakita/memory/`

这 15 个入口基本就能把全局骨架串起来。

## 5. 模块边界速查

这里给一个最容易混淆的边界表：

| 模块 | 解决的问题 | 不负责什么 |
|------|------------|------------|
| `core/agent.py` | 单个 Agent 如何完成任务 | 不负责多 Agent 调度 |
| `agents/orchestrator.py` | 多个 Agent 如何分工 | 不负责组织长期运行 |
| `orgs/runtime.py` | 组织如何持续运行 | 不负责普通会话聊天主线 |
| `skills/loader.py` | SKILL.md 如何变成可用技能 | 不负责插件宿主生命周期 |
| `plugins/manager.py` | 插件如何加载/卸载/隔离 | 不负责 prompt 风格能力封装 |
| `core/brain.py` | 如何调用 LLM | 不负责 HTTP 接口 |
| `api/routes/chat.py` | 如何把聊天流程暴露成 SSE API | 不负责模型底层调用 |
| `prompt/` | prompt pipeline 细节 | 不负责执行工具 |
| `memory/` | 长期记忆与检索 | 不负责会话 API |
| `channels/` | IM 接入 | 不负责会话内主推理 |

## 6. 测试目录怎么读

`tests/` 不是杂乱堆积，基本可以按层看：

- `unit/`：局部规则、配置、安全、路由、工具、插件、模型切换
- `component/`：多模块拼接后的行为
- `orgs/`：组织系统专项测试
- `quality/`：质量与输出选择
- `e2e/`：端到端回归
- `perf/`：性能基线
- `smoke/`：启动和基础冒烟
- `legacy/`：历史遗留测试

如果你准备改某块代码，最好先看对应测试目录，能快速理解“这块真正被要求保证什么行为”。

## 7. 阅读建议

### 7.1 第一遍

第一遍只抓主线，不要试图读完所有实现细节。

目标是搞清楚：

- 入口在哪里
- 请求怎么流转
- 哪些是核心状态
- 哪些是扩展点

### 7.2 第二遍

第二遍按你关心的专题深入：

- 聊天链路
- 多 Agent
- 组织编排
- 安全治理
- 插件生态
- 记忆系统

### 7.3 第三遍

第三遍再进入：

- 失败恢复
- 预算控制
- SSE 协议
- 并发与中断
- 审计与追踪

## 8. 一句话导图

如果必须用一句话概括目录结构，可以这样记：

```text
main / api = 入口
core = 主心脏
agents = 多 Agent
orgs = AI 公司运行时
tools = 执行动作
llm / prompt = 模型与提示词
skills / plugins / MCP = 扩展能力
memory / sessions / identity = 状态与个性
policy_v2 = 安全治理
apps/setup-center = 用户界面
tests = 行为约束
```

## 9. 结尾建议

如果你接下来准备继续深挖，我建议按下面顺序做：

1. 先把 `architecture.md` 通读一遍
2. 对照这份 `source-code-tour.md` 打开对应目录
3. 从 `main.py -> agent.py -> chat.py -> brain.py` 画出你自己的请求链路图
4. 再选一个专题深挖：`orgs/`、`plugins/` 或 `policy_v2/`

这样理解速度会比“从目录树一层层硬读”快很多。
