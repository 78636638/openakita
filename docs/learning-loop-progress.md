# OpenAkita Learning Loop Progress

本文记录 learning loop 的阶段性落地进展，用于阶段验收、问题留档和后续阶段交接。

## 1. 当前状态

- 当前阶段：`Phase 3`
- 当前结论：`Phase 3` 最小低风险自动优化链已完成，当前版本达到“阶段可交付状态”
- 当前策略：继续保持 `Scheduler First`，人工触发仅作为补充入口

## 2. Phase 1 目标回顾

`Phase 1` 的目标是：

- 把 `self_check` 和 `failure_analysis` 统一归一为 `LearningCase`
- 把 `LearningCase` 写入独立 learning store
- 把高价值经验通过现有 `MemoryManager` 写回长期记忆
- 让 `Scheduler` 自动执行 ingest 和 review
- 保持现有记忆体系、现有入口和原主流程不被破坏

## 3. Phase 1 已完成项

### 3.1 新增模块

- `src/openakita/learning/__init__.py`
- `src/openakita/learning/models.py`
- `src/openakita/learning/store.py`
- `src/openakita/learning/case_builder.py`
- `src/openakita/learning/feedback_writer.py`
- `src/openakita/learning/scheduler_hooks.py`

### 3.2 已完成接线

- `self_check.learn_from_check()` 已接入 learning store 和记忆回写
- `failure_analysis` 已接入 `LearningCase` 落库
- `Agent._register_system_tasks()` 已注册：
  - `system_learning_ingest`
  - `system_learning_review`
- `TaskExecutor` 已支持：
  - `system:hourly_learning_ingest`
  - `system:daily_learning_review`

### 3.3 当前行为结论

- `self_check` 失败可直接沉淀为 learning case
- `failure_analysis` 结果可沉淀为 learning case
- learning review 可按阈值将案例写回长期记忆
- 调度器已能自动触发 learning ingest / review
- 现有记忆底座未被替换，仍然通过现有 `MemoryManager.save_user_memory()` 写回

## 4. Phase 1 验证结果

### 4.1 自动化测试

本阶段已补齐并通过以下测试：

- `tests/unit/test_learning_case_builder.py`
- `tests/unit/test_learning_store.py`
- `tests/unit/test_memory_feedback_writer.py`
- `tests/unit/test_scheduler_tasks.py`
- `tests/unit/test_scheduler_executor_status.py`
- `tests/integration/test_learning_selfcheck_flow.py`
- `tests/integration/test_learning_failure_analysis_flow.py`

最近一次回归结果：

- `38 passed`

### 4.2 手工真实调用验证

已在真实工作区数据目录做过一次旁路代码调用验证，验证链路如下：

```text
failure_analysis JSON
-> run_learning_ingest()
-> LearningStore
-> run_learning_review()
-> MemoryManager.save_user_memory()
```

验证结果符合预期，说明 `Phase 1` 的最小自动闭环在真实数据目录中可以正常工作。

说明：

- 手工测试后已清理测试样本、learning case、learning run 记录和测试记忆
- 当前真实工作区中不保留该次手工验证产生的测试脏数据

## 5. Phase 1 范围边界

`Phase 1` 已完成，但仍刻意保持收敛：

- 不新增公开 API
- 不新增前端页面
- 不新增 `MemoryType`
- 不改检索排序
- 不做自动 patch 执行
- 不做 dataset export
- 不做 credit / hit-rate 的正式统计闭环

这些内容顺延到后续阶段处理。

## 6. 当前残余风险

- 尚未做长期运行观察，缺少连续多日 scheduler 自动运行后的稳定性样本
- `learning_case_retention_days` 目前仍是配置预留，尚未实现实际清理任务
- learning run 记录目前只提供最小审计信息，尚未形成阶段报表
- 手工触发目前主要依赖代码调用或调度器任务触发，还没有单独的人机入口

这些风险不阻塞 `Phase 2`，但需要在后续迭代中逐步补齐。

## 7. Phase 2 进入条件

进入 `Phase 2` 前，需要确认以下条件已满足：

- `Phase 1` 自动化测试持续通过
- `Phase 1` 手工验证链路已打通
- learning store 与长期记忆写回逻辑稳定
- Scheduler 注册与执行路径稳定

当前以上条件均已满足。

## 8. Phase 2 目标回顾

`Phase 2` 的目标是：

- 把 `DailyEvaluator` 正式接入 scheduler 主流程
- 让评估结果也能统一沉淀为 `LearningCase`
- 增加最小记忆命中统计
- 把命中统计接入评估摘要与 learning review
- 为后续 `credit` 体系补最小观测骨架
- 仍然不改 `RetrievalEngine` 排序
- 仍然不提前做自动优化执行

## 9. Phase 2 已完成项

### 9.1 DailyEvaluator 接入

- `DailyEvaluator.collect_daily_eval()` 已支持“只收集结果、不执行旧优化动作”
- `run_daily_evaluation()` 已新增到 learning hook
- `Agent._register_system_tasks()` 已注册 `system_daily_evaluation`
- `TaskExecutor` 已支持 `system:daily_evaluation`

### 9.2 评估结果统一入 learning store

- `EvalMetrics / EvalResult` 已可转为 `LearningCase`
- summary case 与异常 trace case 都可写入 `learning_cases`
- `self_check / failure_analysis / daily_evaluation` 三类来源已统一归一

### 9.3 最小命中统计

- learning store 已新增 `learning_hit_stats`
- `RetrievalEngine` 已支持 hit recorder 回调
- `MemoryManager.record_cited_memories()` 已统一记录：
  - 自动检索命中
  - `search_memory` 工具命中
- memory hit 会自动向关联 `LearningCase` 汇总

### 9.4 命中统计摘要接线

- `system:daily_evaluation` 的 `learning_runs.summary_json` 已带：
  - `hit_stats`
- `system:daily_learning_review` 的 `learning_runs.summary_json` 已带：
  - `cases_with_hits`
  - `total_case_hits`
  - `written_case_hits`
  - `hit_stats`

### 9.5 最小 credit 观测骨架

- learning store 已新增 `learning_credit_stats`
- `daily evaluation` 已可基于聚合评估结果记录：
  - `helpful`
  - `neutral`
  - `harmful`
- `daily learning review` 已可读取最近一轮 `daily evaluation` 的：
  - `latest_credit_outcome`
  - `latest_credit_score`
  - `credit_stats`

## 10. Phase 2 验证结果

### 10.1 自动化测试

当前 `Phase 2` 相关核心测试已补齐并通过：

- `tests/unit/test_learning_case_builder.py`
- `tests/unit/test_learning_store.py`
- `tests/unit/test_learning_evaluation_hooks.py`
- `tests/unit/test_memory_retrieval_hit_tracking.py`
- `tests/unit/test_scheduler_tasks.py`
- `tests/unit/test_scheduler_executor_status.py`
- `tests/unit/test_memory_feedback_writer.py`
- `tests/integration/test_learning_selfcheck_flow.py`
- `tests/integration/test_learning_failure_analysis_flow.py`

最近一次回归结果：

- `46 passed`

### 10.2 行为结论

- `DailyEvaluator` 已进入 scheduler 主流程
- 评估结果已能沉淀为 `LearningCase`
- 命中统计已能形成最小报表
- `credit` 已能形成最小观测快照
- 当前 learning loop 已具备：
  - case 沉淀
  - memory 写回
  - hit 观测
  - credit 观测

## 11. Phase 2 范围边界

`Phase 2` 已完成，但仍刻意保持收敛：

- 不做 `retrieval_boost`
- 不根据 `credit` 自动升权/降权
- 不自动修改 prompt / skill / tool 描述
- 不自动执行 patch
- 不新增公开 API 和前端页面
- 不做严格因果 credit，只保留最小观测骨架

这些内容顺延到 `Phase 3` 处理。

## 12. Phase 3 进入条件

进入 `Phase 3` 前，需要确认以下条件已满足：

- `Phase 1 / Phase 2` 自动化测试持续通过
- `Scheduler` 主路径稳定，`daily_evaluation / learning_review` 已能按预期运行
- `LearningCase`、`hit_stats`、`credit_stats` 三层数据已稳定沉淀
- 当前没有引入破坏现有记忆体系的侵入式改造
- 自动优化的目标范围与白名单边界已先文档化

当前以上条件均已满足。

## 13. Phase 3 目标回顾

`Phase 3` 的目标是：

- 在不触碰核心目录自动修改的前提下，实现最小低风险自动优化
- 只允许白名单路径和低风险动作类型进入自动链
- 让动作具备 `shadow -> apply -> verify -> rollback` 的最小闭环能力
- 保持 `Scheduler First`，让自动优化链也可以在无人值守场景下按计划执行
- 保持现有记忆系统、现有 scheduler 主流程和现有入口兼容

## 14. Phase 3 已完成项

### 14.1 最小 planner / executor / verifier

- 已新增：
  - `src/openakita/learning/planner.py`
  - `src/openakita/learning/executor.py`
  - `src/openakita/learning/verifier.py`
  - `src/openakita/learning/shadow.py`
  - `src/openakita/learning/promote.py`
- planner 已可把高价值 `LearningCase` 转成低风险候选动作
- executor 已支持：
  - 白名单路径校验
  - `dry_run`
  - `apply`
  - checkpoint
  - action record
- verifier 已支持：
  - `smoke`
  - `replay`
  - 失败自动回滚

### 14.2 自动优化 scheduler 接线

- `Agent._register_system_tasks()` 已注册：
  - `system_learning_shadow`
  - `system_learning_promote`
  - `system_learning_verifier`
- `TaskExecutor` 已支持：
  - `system:learning_shadow`
  - `system:learning_promote`
  - `system:learning_verifier`

当前自动链顺序为：

```text
system:daily_evaluation   04:30
-> system:daily_learning_review 05:00
-> system:learning_shadow 05:30
-> system:learning_promote 05:45
-> system:learning_verifier 06:00
```

### 14.3 观测摘要收口

- `daily_learning_review` 的 `learning_runs.summary_json` 现在会同时汇总：
  - `latest_shadow_metrics`
  - `latest_shadow_run`
  - `latest_promote_metrics`
  - `latest_promote_run`
  - `latest_verifier_metrics`
  - `latest_verifier_run`
- `shadow / promote / verifier` 都已具备：
  - 原始 run 快照
  - 稳定指标摘要

### 14.4 当前行为结论

- learning loop 已具备：
  - case 沉淀
  - memory 写回
  - hit 观测
  - credit 观测
  - planner 候选动作生成
  - shadow dry-run
  - promote apply
  - verifier 验证与回滚
- 最小低风险自动优化链已经可以在 scheduler 主路径中自动运行
- 现有记忆底座未被替换，learning 仍然是增强层而不是替换层

## 15. Phase 3 验证结果

### 15.1 自动化测试

本阶段已新增或扩展并通过以下核心测试：

- `tests/unit/test_learning_planner.py`
- `tests/unit/test_learning_executor.py`
- `tests/unit/test_learning_verifier.py`
- `tests/unit/test_learning_shadow.py`
- `tests/unit/test_learning_promote.py`
- `tests/unit/test_learning_review_hooks.py`
- `tests/unit/test_scheduler_tasks.py`
- `tests/integration/test_learning_failure_analysis_flow.py`

最近一轮与 `shadow / promote / verifier / review summary / scheduler` 相关的定向回归结果：

- `33 passed`

### 15.2 行为判断

- planner 能按低风险策略生成候选动作
- shadow 不会真实写入目标文件
- promote 仅从既有 `dry_run` 记录提升，不绕过预演阶段
- verifier 能对已应用动作做最小验证，并在失败时回滚
- review 能汇总最近一次 shadow / promote / verifier 运行结果

## 16. Phase 3 范围边界

`Phase 3` 当前版本已完成，但仍刻意保持收敛：

- 不做复杂 canary / 分层推广策略
- 不做 dataset exporter
- 不新增公开 API 和前端页面
- 不自动修改 `src/openakita/core/`、`memory/`、`llm/`、`agents/`、`scheduler/`
- 不做基于 credit 的自动升权 / 降权
- 不做更复杂的验证策略，只保留 `smoke / replay`

这些内容属于后续增强项，不阻塞当前阶段交付。

## 17. 当前残余风险

- 尚未做连续多日无人值守运行观察，缺少长期稳定性样本
- `learning_case_retention_days` 仍是配置预留，尚未实现实际清理任务
- 目前是最小 promote 入口，不是完整 promoter / canary 基建
- 观测摘要以“最近一次已完成 run”为准，不是实时 dashboard

这些风险说明当前版本仍是“务实最小交付”，但不影响本阶段验收结论。

## 18. 阶段交付判断

当前版本判断为：`达到 Phase 3 阶段可交付状态`。

理由：

- 已满足 `Scheduler First` 的主设计方向
- 已形成 `shadow -> promote -> verifier` 的最小自动优化闭环
- 已具备 action record、checkpoint、验证与回滚
- 已把自动优化结果纳入统一 review 摘要链路
- 没有破坏现有记忆体系与现有主入口

同时也要明确：

- 这表示“当前阶段目标已经完成并可交付”
- 不表示“长期完整设计已全部实现”
- 后续若继续推进，应进入增强型 promoter / canary / exporter / UI 等扩展项

## 19. 一句话结论

`Phase 1` 到 `Phase 3` 的务实最小闭环现已打通：系统已能在不破坏现有记忆体系的前提下，通过 `Scheduler` 自动完成 learning case 沉淀、记忆回写、低风险候选动作预演、受控 apply、验证与回滚，并达到当前阶段的可交付状态。

## 20. Phase 3 收尾总结

本阶段收尾结论如下：

- `Phase 3` 已按“务实最小闭环”范围完成交付
- 自动优化链已具备：
  - 候选动作生成
  - shadow 预演
  - 受控 apply
  - 最小验证
  - 失败回滚
- 自动优化链已接入 scheduler 主路径，不再只是代码级手工入口
- 观测摘要已统一进入 `daily_learning_review`
- 当前版本可作为下一阶段增强迭代的稳定基线

当前阶段收尾后，建议把后续工作从“打通最小闭环”切换为“增强稳定性、增强治理能力、增强可观测性”。

## 21. 下一阶段建议清单

建议下一阶段按以下优先级推进：

### 21.1 优先级 A：稳定性与运维收口

- 增加连续多日无人值守运行观察
- 增加 learning run 的周期性健康检查与异常告警线索
- 实现 `learning_case_retention_days` 对应的实际清理任务
- 补一份面向运维的回滚 / 停用 / 恢复手册

### 21.2 优先级 B：增强型 promoter / canary

- 在最小 `promote` 之上补完整的 promoter 策略层
- 支持更细粒度的 promote 准入条件
- 增加 canary / 分批推广 / 自动停止策略
- 让 `promote` 不只是“从 dry_run 提升到 apply”，而是具备更强治理语义

### 21.3 优先级 C：可观测性增强

- 将 `shadow / promote / verifier` 指标整理成更稳定的日报 / 周报摘要
- 增加趋势指标，例如：
  - shadow 命中率
  - promote 成功率
  - verifier 回滚率
- 视需要补 dashboard 或导出报表能力

### 21.4 优先级 D：能力扩展

- 评估是否引入更丰富的验证策略，超出 `smoke / replay`
- 评估是否引入 dataset exporter
- 评估是否增加公开 API、前端页面、手工补跑入口
- 继续保持不破坏现有记忆体系与核心目录自动改写边界

### 21.5 推荐推进顺序

推荐顺序如下：

1. 先做稳定性观察与清理任务
2. 再做增强型 promoter / canary
3. 再做可观测性增强
4. 最后再考虑 API / UI / exporter 等扩展面

这样可以保证下一阶段仍然延续当前项目的务实策略：

- 先把自动链跑稳
- 再把自动链管严
- 最后再把自动链做大
