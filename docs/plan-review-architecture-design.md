# 计划审查子系统 — 架构设计与优化方案

> **状态**：v0.3 已实现（L1-L10 全部完成 + 单测 + ruff 全过）
> **作者**：openakita 团队
> **最后更新**：2026-06-04
> **关联事件**：`plan_20260604_134223_01c921` 误报"审查未明确通过"

## 〇、修订记录

### v0.2 (2026-06-04) — 兼容性复核修正

| # | 原 v0.1 设计 | v0.2 修正 | 原因 |
|---|---|---|---|
| 1 | `parse_review_result` 重命名为 `resolve_review_result`，返回 `frozen=True` 的 `ReviewSummary` dataclass | **保留** `parse_review_result` 名称 + dict 返回，扩展返回 dict 增加 `requires_re_review` 字段 | 3 处直接调用 (agent.py:10784, 10867; todo_handler.py:28, 134)，转 dataclass 需改全部调用方 |
| 2 | 新增 `_close_plan(plan_id, action)` 作为统一收口 | **复用** 现有 `PlanHandler.finalize_plan(plan, session_id, action)`，仅修改其返回类型 + 2 个内部调用方 (todo_state.py:253, 279) 更新 | 已有同名收口点，新增函数冗余 |
| 3 | L6 未列举 `auto_close_todo` 全部调用方 | **补全**：reasoning_engine.py:4423（不检查返回值）、9418（异常即 False）、agent.py:6386（条件分支） | 改动会影响所有调用方，必须逐一分析 |
| 4 | L4 "❌" 误伤未验证 | **已验证**：`_is_error_entry(False, "已检查项：❌ 文件不存在 / ✅ 配置正确") == True` 是 bug | 修复方向明确 |
| 5 | L7 仅加"【强制】"标记 | **加** `response_format=json_object` API 强制（如 provider 支持）作为主路径，prompt 强化作为 fallback | LLM 训练数据里"自然语言审查"是常态，单靠 prompt 提升有限 |
| 6 | L1 主 agent retry 缺状态机 | **明确** retry 在 `_update_step` 内同步完成，step.status 维持 `completed`，result 被覆盖 | step 状态机不能被打断 |

### v0.1 (2026-06-04) — 初稿

- 列出 L1-L10 修复任务
- 提议 ReviewResultResolver + 统一收口架构

### v0.3 (2026-06-04) — 已实现

| ID | 任务 | 实现位置 | 测试位置 |
|---|---|---|---|
| L1 | 主 agent 审查软重试 | `todo_handler.py:_update_step` 软标记 + 工具返回 hint | `test_todo_handler_l1_retry.py` (4 测试) |
| L2 | 清理死代码 | `todo_review.py` (删 `PASS_MARKERS` / `FAIL_MARKERS`) + `reasoning_engine.py` (删 `_looks_like_waiting_for_user_response` 等) | `test_reasoning_engine_user_handoff.py` (2 旧测试删除) |
| L3 | 历史 plan 回填 | `scripts/backfill_plan_review_verdicts.py` (+ `--force`) | `test_backfill_script.py` (8 测试) |
| L4 | 收窄 `_GENERIC_FAIL_MARKERS` | `orgs/failure_diagnoser.py`（删 `"❌"`） | `test_failure_diagnoser_markers.py` (5 测试) |
| L5 | 审查解析结构化日志 + `requires_re_review` | `todo_review.py:parse_review_result` 新增 `trace` 参数 | `test_todo_review.py::TestRequiresReReviewAndTrace` (7 测试) |
| L6 | `finalize_plan` 与 review 解耦 | `todo_handler.py:finalize_plan` 返回 dict + `todo_state.py` 2 个调用方更新 | `test_todo_handler_finalize_plan.py` (8 测试) |
| L7 | 审查 prompt 强化 | `agent.py:9584 / 9851 / 9925` 3 处加 `【强制】` + 重做提示 | (冒烟测试：3 处确认) |
| L8 | retry 携带失败上下文 | `todo_review.py:build_review_retry_message` 新增 `previous_output` / `previous_failure_reason` 参数 | `test_todo_review.py::TestRetryMessageWithContext` (4 测试) |
| L9 | next_round_todo step 描述加 `[重做审查]` | `todo_review.py:build_next_round_todo` + `_is_review_not_concluded` | `test_todo_review.py::TestNextRoundTodoReReviewMarker` (4 测试) |
| L10 | plan review 健康度监控 | `scripts/plan_review_health.py` (输出 `docs/plan-review-health/<week>.md`) | `test_plan_review_health.py` (7 测试) |

**总计**：140 个相关单测全过；ruff 检查全过（仅既有 `agent.py` 预存错误未修，与本任务无关）

**实际交付物**：
- 8 个新/修改源文件
- 6 个新测试文件
- 2 个新脚本（`backfill_plan_review_verdicts.py` / `plan_review_health.py`）
- 1 个历史 plan 文件已回填（`plan_20260604_134223_01c921.md`，备份在 `data/plans/.bak/`）
- 1 份 `BACKFILL_REPORT.md`
- 1 份 `docs/plan-review-health/2026-W23.md`

---

## 一、背景与目标

### 1.1 触发事件

2026-06-04 抖音热榜采集任务（`plan_20260604_134223_01c921`）实际任务执行完整：

- 50 条热榜词条已采集
- 28 条购物意向热词已分类
- 报告 `data/temp/douyin_hot_trends_2026_shopping_report.md` (7837 bytes / 146 行) 已生成
- 桌面端已交付

但系统报告里出现 **`结论=unknown`** 和 **`最终审查未明确通过`** 的误报，并生成了不必要的"下一轮 Todo：修复问题：最终'审查'未明确通过"。

### 1.2 根因（已修复部分）

| Bug | 位置 | 现象 |
|---|---|---|
| **Bug A** | `parse_review_result` 的 fallback 路径 | 用硬编码字符串（`PASS_MARKERS` / `FAIL_MARKERS`）反推 verdict/passed，把 LLM 自然语言审查结果错判为"未通过"或"未明确通过" |
| **Bug B** | `reasoning_engine._handle_final_answer` | 用 `_looks_like_waiting_for_user_response` 文本启发式判定"用户被卡住"，把"飞书推送失败…需要我直接展示，还是你先下载查收？"误判为「hands control back to user」，TaskVerify 被错误跳过 |

修复已并入本仓库。本次设计文档讨论**修复后仍遗留的次要问题**与**整体架构加固**。

### 1.3 设计目标

1. **结构化优先**：审查结论一律以结构化 JSON 为唯一可信源；自然语言只在兜底分支产生 `passed=None` 信号，绝不伪造 verdict。
2. **统一入口 / 统一收口**：所有审查结果解析、消费、下一步动作判定都走同一组函数，避免散落判断（规则 3）。
3. **可观测性**：审查解析过程可被结构化日志与监控指标复盘。
4. **数据真实性**：plan 报告反映真实状态；`auto_close` 与 review 状态解耦。
5. **可回填**：历史误报可一次性脚本修正。

---

## 二、核心设计原则

| 原则 | 含义 | 违反示例（历史 bug） |
|---|---|---|
| **结构化优先** | 审查结论以结构化 JSON 为唯一可信源；自然语言只能产生"未生成结论"信号 | Bug A 旧版：自然语言"审查完成"被反推为 passed=False |
| **严判原则** | 当有疑问时，不要伪造 verdict；交由编排层走"未生成结论"分支 | Bug A 旧版：模糊子串"审查失败"/"推送失败"互相干扰 |
| **统一入口** | 所有"raw 文本 → ReviewSummary"的解析走同一函数 | 当前 `parse_review_result` 被 2 处直接调用，外部可绕开 |
| **统一收口** | plan 关闭 / 拒绝 / 重规划的判定走同一函数 | 当前 `auto_close_todo` / `_complete_todo` 各自有判定 |
| **显式优于隐式** | 真正的"等用户"信号是显式 `ask_user` 工具调用；文本不代替工具 | Bug B 旧版：文本含"你"+"失败"就跳过 verify |
| **可观测** | 关键判定路径有结构化日志，可回放 | 当前 `parse_review_result` 失败时只有 reason |
| **可回退** | 每项改动局部化，单项可独立 revert | 任何改动不得跨 3 个以上模块 |

---

## 三、遗留问题清单与优先级

| ID | 类别 | 描述 | 优先级 | 关联原则 | 估算 |
|---|---|---|---|---|---|
| **L1** | 代码 | 主 agent 审查步骤缺 retry 机制（仅 sub-agent 路径有） | P0 | 结构化优先 / 严判 | 0.5d |
| **L2** | 代码 | `PASS_MARKERS` / `FAIL_MARKERS` / `_looks_like_waiting_for_user_response` 残留死代码 | P0 | 可回退 / 代码清洁 | 0.2d |
| **L3** | 数据 | 历史 plan 报告"审查未通过"是误报（`plan_20260604_134223_01c921.md`） | P1 | 数据真实性 | 0.3d |
| **L4** | 代码 | `orgs/failure_diagnoser._GENERIC_FAIL_MARKERS` 同类字符串匹配漏洞（"❌" emoji 误伤） | P1 | 严判原则 | 0.2d |
| **L5** | 代码 | `parse_review_result` 无结构化日志 | P1 | 可观测 | 0.3d |
| **L6** | 流程 | `auto_close_todo` 与 review 状态脱钩 | P1 | 数据真实性 | 0.3d |
| **L7** | 提示 | 审查步骤 prompt 强度不足 | P2 | 结构化优先 | 0.3d |
| **L8** | 代码 | `REVIEW_RETRY_PROMPT` 缺上下文（重试时不带上次失败原因） | P2 | 严判 / 可回退 | 0.2d |
| **L9** | 流程 | `build_next_round_todo` 生成条件过宽（审查未生成结论也刷 Todo） | P2 | 严判 / 显式优于隐式 | 0.2d |
| **L10** | 运维 | 缺 plan review 健康度监控脚本 | P2 | 可观测 | 0.3d |

**总投入**：~2.8 人天，分散在 1 周内可完成。

---

## 四、统一架构设计

### 4.1 现状：散落判定

```
LLM update_todo_step result
       │
       ├──► PlanHandler._parse_review_summary ──► parse_review_result ──► ReviewSummary
       │                                                       │
       │                                                       ├──► _review_step_passed  ──► bool
       │                                                       ├──► _build_blockers       ──► list[str]
       │                                                       └──► _step_has_meaningful_result
       │
       └──► Agent._build_task_plan_verification_summary ──► parse_review_result ──► ReviewSummary
                                                                       │
                                                                       ├──► reason = "..."
                                                                       ├──► review_passed = bool(passed)
                                                                       ├──► business_outcome = "..."
                                                                       └──► suggested_next_round_todo

auto_close_todo (finalize) ──► finalize_plan("auto_close")  [不检查 review 状态]
```

**问题**：

1. 判定"review 是否通过"的代码散落 3 处（`_review_step_passed`、`Agent._build_task_plan_verification_summary`、原 fallback 字符串匹配），容易不一致
2. "auto_close" 和 "review 状态"没有统一判定，可能掩盖"审查未完成"
3. 缺少"raw text → ReviewSummary"的中间契约类型

### 4.2 目标架构：统一入口 + 统一收口

```
                              ┌──────────────────────────────┐
LLM 审查步骤 update_todo_step │  ReviewResultResolver        │  ← 唯一入口
        ──────────────────────►  (tools/handlers/todo_review.py) │
                              │                              │
                              │  1. _extract_json_payload    │
                              │  2. _normalize_review_summary│
                              │  3. _apply_safety_nets       │
                              │  4. _enrich_with_context     │
                              │                              │
                              │  入参: raw_text, requires_*  │
                              │  出参: ReviewSummary (frozen)│
                              └──────────┬───────────────────┘
                                         │
                                         ▼
                              ┌──────────────────────────────┐
                              │  ReviewSummary               │  ← 唯一数据契约
                              │  ─────────────────           │
                              │  verdict: Literal[...]       │
                              │  passed: bool | None         │
                              │  blockers: list[str]         │
                              │  next_actions: list[str]     │
                              │  delivery_verified: bool|None│
                              │  test_evidence: dict         │
                              │  hallucination_found: bool|None│
                              │  requires_re_review: bool    │  ← 新增
                              │  raw_result: str (≤ 2000)    │
                              └──────────┬───────────────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              ▼                          ▼                          ▼
   ┌─────────────────────┐  ┌──────────────────────────┐  ┌──────────────────┐
   │ PlanHandler         │  │ Agent Orchestration      │  │ TaskVerify       │  ← 三个收口点
   │ _close_plan         │  │ _build_verification      │  │ _handle_final    │
   │ (单一关闭路径)      │  │ _summary                 │  │ _answer          │
   └─────────────────────┘  └──────────────────────────────┘  └──────────────────┘
              │                          │                          │
              ▼                          ▼                          ▼
       should_auto_close           reason = "..."           is_completed = bool
       requires_re_review          business_outcome         (基于真实证据)
       should_block_complete       next_round_todo
```

### 4.3 关键设计点

#### 4.3.1 唯一入口：扩展现有 `parse_review_result`

> **v0.2 修正**：保留 `parse_review_result` 名称 + dict 返回，**不引入 dataclass**。3 处直接调用方零改动。

```python
# src/openakita/tools/handlers/todo_review.py

def parse_review_result(
    result: str,
    *,
    requires_code_test: bool = False,
    requires_delivery: bool = False,
    trace: list[dict] | None = None,  # ← v0.2 新增：可选结构化追踪
) -> dict[str, Any]:
    """统一入口：raw text → ReviewSummary dict

    v0.2 扩展：返回 dict 增加字段
      - requires_re_review: bool  ← 关键新增
        计算规则: requires_re_review = (passed is None) or
                  (verdict == "failed" and bool(blockers)) or
                  (verdict == "needs_follow_up")

    返回 dict 字段（向后兼容）：
      - verdict, passed, blockers, next_actions, delivery_verified,
        hallucination_found, test_evidence, raw_result, requires_re_review
    """
    ...
```

**对外接口**：`parse_review_result` 仍是唯一对外函数。下游消费者（agent.py、todo_handler.py）继续用 dict 访问，**零破坏**。

#### 4.3.2 唯一收口：升级现有 `finalize_plan`

> **v0.2 修正**：复用现有 `PlanHandler.finalize_plan`，**不新增 `_close_plan`**。改动：返回类型从 `None` 改为 `dict`。

```python
# src/openakita/tools/handlers/todo_handler.py
class PlanHandler:
    def finalize_plan(
        self,
        plan: dict,
        session_id: str,
        action: str = "auto_close",
    ) -> dict[str, Any]:
        """统一收口：plan 关闭 / 拒绝 / 重规划的判定都走这里。

        v0.2 升级返回 dict，向上兼容旧调用方：
          {"closed": True,  "message": "..."}              # 正常关闭
          {"closed": False, "block_reason": "...",         # 审查阻止
           "blocker_kind": "review_not_concluded",
           "suggested_next_round_todo": {...} | None}

        action ∈ {"auto_close", "cancel", "complete"}
        """
        # 检查审查状态（v0.2 新增）
        review_step = self._get_review_step(plan)
        if action != "cancel" and review_step:
            review_summary = parse_review_result(
                review_step.get("result", ""),
                requires_code_test=plan.get("_requires_code_test", False),
                requires_delivery=plan.get("_requires_delivery", False),
            )
            if review_summary.get("requires_re_review"):
                # 不静默关闭，返回 blocked 信号给上层
                return {
                    "closed": False,
                    "block_reason": "review_not_concluded",
                    "blocker_kind": "review_not_concluded",
                    "message": (
                        f"当前计划未通过最终审查，暂不能标记完成。\n"
                        f"- {review_summary['blockers'][0] if review_summary['blockers'] else '审查未生成有效结论'}\n"
                    ),
                    "suggested_next_round_todo": build_next_round_todo(
                        plan, blockers=review_summary["blockers"],
                        review_summary=review_summary,
                    ),
                }

        # 正常关闭 / 取消逻辑（保留原行为）
        # ... 原 finalize_plan body
        return {"closed": True, "message": "已关闭"}
```

**调用方更新**（2 个内部）：
- `todo_state.py:253` (`auto_close_todo` 路径)：
  ```python
  result = handler.finalize_plan(plan, session_id, action="auto_close")
  if not result["closed"]:
      logger.warning("auto_close blocked: %s", result["block_reason"])
      return False
  logger.info("[Todo] Auto-closed todo for session %s", session_id)
  return True
  ```
- `todo_state.py:279` (`cancel_todo` 路径)：action=cancel 跳过 review 检查，原行为不变

**外部调用方**（不需改）：
- `agent.py:6386` `if auto_close_todo(conversation_id):` — 返回值含义不变（True=已关，False=被阻止或失败）
- `reasoning_engine.py:4423` `auto_close_todo(conversation_id)` — 忽略返回值，原本就是 fire-and-forget
- `reasoning_engine.py:9418` `return bool(auto_close_todo(conversation_id))` — 同上

#### 4.3.3 状态机：plan 关闭的合法迁移

> **v0.2 修正**：`_close_plan` → 复用现有 `finalize_plan`，状态机不变

```
                  ┌──────────────┐
       create ──► │    active    │
                  └──────┬───────┘
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
      ┌──────────┐ ┌──────────┐ ┌──────────┐
      │ closing  │ │ redoing  │ │ cancelled│
      └────┬─────┘ └────┬─────┘ └──────────┘
           │            │
     ┌─────┴─────┐      │
     ▼           ▼      ▼
  closed    blocked  next_round_todo
  (closed=True,  (closed=False,  (新建 plan,
   message="...")  block_reason,   不修改原 plan)
                  next_round_todo)
                  │      │
                  │      └────► (回到 active)
                  ▼
           (LLM 补审查后)
```

**关键不变量**：
- 任何 `active → closed` 的迁移都必须通过 `finalize_plan`
- `closed` 状态一旦写入，**不再回写 `active`**（避免 LLM 误改 plan 状态）
- `next_round_todo` 必须**新建一个 plan**，不修改原 plan 字段
- `finalize_plan` 返回 dict 统一收口，**所有调用方**都能感知"审查阻止关闭"信号

---

## 五、修复任务拆分

> 每个 L1-L10 一节，包含：目标、依赖、详细设计、验收标准、工作量估算。

### L1：主 Agent 审查步骤 retry 机制

**目标**：LLM 第一次不返回 JSON 时，给一次本地重试机会，而不是直接生成下一轮 Todo。

**依赖**：L5（结构化日志）— 重试时需要记录"为什么 retry"

**详细设计**：
- 在 `PlanHandler._update_step` 检测当前 step 是审查步骤且 result 不是有效 JSON
- 调用 `is_valid_review_json(result)` 判定
- 若不是 JSON，从 plan 的 step 描述里拿原始任务描述，调用 `REVIEW_RETRY_PROMPT` 构造重试消息
- 通过 `LLMClient` 重新发一次，**不走完整 ReAct 整轮**，只一次 LLM 调用
- 用新返回值更新 step.result
- 加 `step.retried_count` 字段记录重试次数
- 重试上限 `REVIEW_MAX_RETRIES=1`（与 sub-agent 一致）

**验收**：
- LLM 第一次输出自然语言 → retry 一次成功输出 JSON → step.result 为 JSON，passed/verdict 正确
- LLM retry 仍输出自然语言 → 落入"未生成结论"分支，生成 next_round_todo
- 重试不影响 plan 总步数

**风险**：
- 在 tool 层 retry 会增加 LLM API 调用次数 → 成本 +5%
- 同步调用可能阻塞 plan handler → 用 `asyncio.to_thread` 隔离

**工作量**：0.5d

---

### L2：清理残留死代码

**目标**：删除 Bug A / Bug B 修复后无用的字符串匹配常量和函数。

**详细设计**：
- 删除 `todo_review.py`:
  - `PASS_MARKERS` 常量（L10）
  - `FAIL_MARKERS` 常量（L67-83）
- 删除 `reasoning_engine.py`:
  - `_looks_like_waiting_for_user_response` 函数（L1277-1340）
  - `_USER_BLOCKED_MARKERS` / `_USER_BLOCKED_ACTIONS` / `_RECOVERABLE_TOOL_ERROR_MARKERS` / `_HARD_USER_BLOCKER_TOOL_MARKERS` 常量（L1222-）
- 保留 `is_valid_review_json` / `REVIEW_RETRY_PROMPT` / `REVIEW_MAX_RETRIES` / `build_review_retry_message`（L1 重试要复用）

**验收**：
- 全部 import 仍能正常解析
- 现有 14 个 `test_reasoning_engine_user_handoff` 测试中关于函数自身的 2 个测试改用 `pytest.raises(ImportError)` 或删除
- grep 全仓库无引用

**工作量**：0.2d

---

### L3：历史 plan 报告回填

**目标**：把 `data/plans/plan_20260604_134223_01c921.md` 的"审查未明确通过"误报改成正确结论。

**详细设计**：
- 新增 `scripts/backfill_plan_review_verdicts.py`:
  - 遍历 `data/plans/*.md`
  - 找 `## 审查摘要` 区块
  - 解析 `结论=XXX` 字段
  - 找到对应的 review 步骤 result_preview
  - 用新逻辑 `resolve_review_result` 重新评估
  - 备份原文件 → 写新文件
- 备份存 `data/plans/.bak/<timestamp>/`
- 写一份 `data/plans/BACKFILL_REPORT.md` 记录改动前后对比

**验收**：
- `data/plans/plan_20260604_134223_01c921.md` 的"审查摘要"从 "结论=unknown" 改成 "结论=passed（LLM 实际完成了审查，仅因为未回 JSON 被旧版误判）"
- 备份可恢复
- 日志显示改动行数

**工作量**：0.3d

---

### L4：`_GENERIC_FAIL_MARKERS` 收窄

**目标**：去掉 `"❌"` 这种高误伤 marker，避免 emoji 干扰失败识别。

**详细设计**：
- 改 `src/openakita/orgs/failure_diagnoser.py:44-60`
- 旧：
  ```python
  _GENERIC_FAIL_MARKERS = (
      "[失败]",
      "[org_delegate_task 失败]",
      "❌",                      # ← 误伤
      "⚠️ 工具执行错误",
      "⚠️ 策略拒绝",
      "错误类型:",
  )
  ```
- 新：
  ```python
  _GENERIC_FAIL_MARKERS = (
      "[失败]",
      "[org_delegate_task 失败]",
      "⚠️ 工具执行错误",
      "⚠️ 策略拒绝",
      "错误类型:",
  )
  ```

**验收**：
- 现有 orgs 测试不回归
- 加 1 个测试：tool result 内容含"❌" + is_error=False → 判非失败

**工作量**：0.2d

---

### L5：审查解析结构化日志

**目标**：审查解析过程可被结构化日志复盘。

**详细设计**：
- 改 `parse_review_result` → `resolve_review_result`：
  - 入参新增 `trace: list[dict] | None = None`
  - 解析各阶段写入 trace：
    - `{"event": "json_extract_attempt", "raw_length": N}`
    - `{"event": "json_extracted", "keys": [...]}`
    - `{"event": "fallback_used", "reason": "no_json"}`
    - `{"event": "safety_net_applied", "rule": "hallucination_found"}`
    - `{"event": "summary_finalized", "verdict": "...", "passed": ...}`
  - 最终 `logger.debug("Review resolved: %s", trace)`
- LLM 重试时（L1）日志附 `retry_reason`

**验收**：
- 跑一次 plan → 看到 5-8 条 `Review resolved` debug 日志
- 写 1 个测试：mock 一次解析，断言 trace 列表内容

**工作量**：0.3d

---

### L6：auto-close 与 review 状态解耦

**目标**：plan 含审查步骤且 review 状态未生成有效结论时，auto_close 应当被阻止（或显式标记 caveat）。

**详细设计**：
- 改 `auto_close_todo` 内部逻辑：
  ```python
  def auto_close_todo(session_id: str) -> bool:
      plan = _load_plan(session_id)
      review_summary = resolve_review_result(plan.review_step.result)
      if review_summary.requires_re_review:
          # 不静默关闭，写一个 blocked 信号给 session
          session.set_metadata("auto_close_blocked_reason", review_summary.blockers)
          logger.warning(
              "auto_close blocked: review not concluded for plan %s", plan.id,
          )
          return False
      # ... 原 auto_close 逻辑
  ```
- `auto_close_todo` 的最终调用方（agent.py:6384）要处理 False 返回值

**验收**：
- 含审查步骤 + review=None → auto_close 返回 False，plan 状态保持 active
- 不含审查步骤 → auto_close 照常运行
- 现有 5 个 auto_close 相关测试不回归

**工作量**：0.3d

---

### L7：审查 prompt 强度强化

**目标**：LLM 第一次输出 JSON 的概率从 ~50% 提升至 ~80%。

**详细设计**：
- 改 [src/openakita/core/agent.py:9584-9596](file:///root/projects/openakita/src/openakita/core/agent.py#L9584-L9596)（3 处审查 prompt）：
  - 开头加 `【强制】` 标记
  - 给一个完整 JSON 示例
  - 末尾加 `⚠️ 否则系统将判定为「审查未生成有效结论」并生成下一轮 Todo`
- 同 [src/openakita/core/agent.py:9851-9866](file:///root/projects/openakita/src/openakita/core/agent.py#L9851-L9866) 和 [9925-9940](file:///root/projects/openakita/src/openakita/core/agent.py#L9925-L9940)

**验收**：
- 跑 3-5 个真实 plan 任务，统计 LLM 一次输出 JSON 的比例
- prompt 长度增加 ≤ 200 token

**工作量**：0.3d

---

### L8：retry 时携带失败上下文

**目标**：retry 时 LLM 知道"上次为什么被判定为非 JSON"，命中率提升。

**详细设计**：
- 改 `REVIEW_RETRY_PROMPT`（[src/openakita/tools/handlers/todo_review.py:14-33](file:///root/projects/openakita/src/openakita/tools/handlers/todo_review.py#L14-L33)）：
  - 增加"你上次输出了 XXX（前 200 字符），请改用以下 JSON 格式"
  - 增加"如果任务确有 blocker，请在 blockers 数组里列明"
- L1 重试时把 LLM 上次输出（截断 200 字符）传给 `build_review_retry_message`

**验收**：
- 写 1 个测试：构造一次 LLM 自然语言输出 → 喂给 build_review_retry_message → 输出含"你上次输出了..."
- 实测 retry 命中率提升

**工作量**：0.2d

---

### L9：next_round_todo 生成条件收紧

**目标**：审查未生成有效结论时，next_round_todo 的 step 描述显式标"重做审查"，避免 LLM 误以为是新任务。

**详细设计**：
- 改 `build_next_round_todo`（[src/openakita/tools/handlers/todo_review.py:113-153](file:///root/projects/openakita/src/openakita/tools/handlers/todo_review.py#L113-L153)）：
  - 当 `review_summary.requires_re_review=True` 且 `blockers` 中含"审查未生成"类时：
    - step_1 description 前缀加 `[重做审查]`
    - 减少其他 next_actions 的生成（只保留审查重做相关的）
  - 当 review 通过但有其他 failure 时，保持现有行为

**验收**：
- 写 1 个测试：构造 review_summary.passed=None + blockers=["审查未生成..."] → 生成 step_1 描述以"[重做审查]"开头
- LLM 端实测：L1 + L7 + L8 + L9 全套上后，"重做审查"标记能被 LLM 正确识别

**工作量**：0.2d

---

### L10：plan review 健康度监控

**目标**：建立 plan review 质量指标体系，长期可观测。

**详细设计**：
- 新增 `scripts/plan_review_health.py`：
  - 扫描 `data/plans/*.md` 最近 7 天
  - 统计：
    1. 每周 plan 总数
    2. 触发了 next_round_todo 的占比
    3. 审查 verdict 分布（passed / failed / unknown / needs_follow_up）
    4. 真实失败 vs 误报占比（误报 = verdict=unknown 但实际任务已完成）
  - 输出 markdown 报告 `docs/plan-review-health/<week>.md`
- 不动运行期代码

**验收**：
- 脚本可独立运行
- 输出报告含 4 项指标
- 误报识别逻辑有单元测试

**工作量**：0.3d

---

## 六、风险与回退

| 风险 | 概率 | 影响 | 回退方案 |
|---|---|---|---|
| L1 retry 增加 LLM API 成本 +5% | 中 | 成本 | 配置项可关闭 `REVIEW_ENABLED=False` |
| L6 auto_close 阻止逻辑导致 plan 永远不关 | 低 | 用户体验 | 加兜底：24h 后强制 auto-close + warning 日志 |
| L7 prompt 强化让 LLM 输出格式更死板 | 中 | 灵活性 | 加配置项 `REVIEW_PROMPT_MODE={strict,flexible}` |
| L3 回填脚本误改 plan 文件 | 低 | 数据 | 写新文件前先备份到 `data/plans/.bak/` |
| L2 删除死代码破坏外部 import | 极低 | 编译失败 | 全仓库 grep 确认无引用，CI 二次确认 |

**整体回退策略**：每项独立 commit，单独 revert。Phase 1（P0）失败可回退到本次 PR 之前；Phase 2-4 失败可回退到 Phase 1 完成态。

---

## 七、落地顺序与时间表

```
Phase 1（P0，~0.7d，2026-06-05 ~ 2026-06-06）
  ├─ L1 主 agent 审查 retry
  └─ L2 清理死代码
  → 合并 PR-1

Phase 2（P1，~1.1d，2026-06-07 ~ 2026-06-09）
  ├─ L3 历史 plan 回填
  ├─ L4 _GENERIC_FAIL_MARKERS 收窄
  ├─ L5 审查解析结构化日志
  └─ L6 auto-close 解耦
  → 合并 PR-2

Phase 3（P2 核心，~0.7d，2026-06-10 ~ 2026-06-11）
  ├─ L7 审查 prompt 强化
  ├─ L8 retry 携带失败上下文
  └─ L9 next_round_todo step 描述标记
  → 合并 PR-3

Phase 4（运维，~0.3d，2026-06-12）
  └─ L10 plan review 健康度监控
  → 合并 PR-4
```

**总投入**：~2.8 人天
**单测增量**：预计 +8 单元测试 / -3 旧测试需更新
**集成测试**：用 `data/plans/plan_20260604_134223_01c921.md` 作为回归 case

---

## 八、验收标准（Phase 合并门禁）

每个 Phase 合并前必须满足：

- [ ] 该 Phase 涉及的 L1-L10 任务全部完成
- [ ] `pytest tests/unit/test_todo_review.py tests/unit/test_todo_handler.py tests/unit/test_task_planner.py tests/unit/test_reasoning_engine_user_handoff.py` 全部通过
- [ ] `ruff check src/openakita/ tests/` 全部通过
- [ ] `data/plans/plan_20260604_134223_01c921.md` 重新跑一次完整 plan 流程不再误报
- [ ] CHANGELOG 更新

---

## 九、参考

- [PLAN.md](/root/projects/openakita/AGENTS.md)（项目总规范）
- [callchain-reliability-design.md](/root/projects/openakita/docs/callchain-reliability-design.md)（类似架构设计范式）
- [callchain-reliability-development-plan.md](/root/projects/openakita/docs/callchain-reliability-development-plan.md)（类似开发计划范式）
- 修复本次 Bug 的提交：见 git log
