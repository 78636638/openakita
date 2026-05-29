# OpenAkita 记忆与自主进化闭环开发步骤规划

本文是面向研发执行的开发路线文档。

目标不是重复描述方案，而是把 [learning-loop-implementation-spec.md](file:///root/projects/openakita/docs/learning-loop-implementation-spec.md) 进一步拆成一套可逐项实施、逐项验证、逐项回归的开发计划，便于后续按步骤推进、测试和验收。

## 1. 开发原则

- 优先复用现有 `self_check / memory / scheduler / evaluation`
- 优先打通最小自动闭环，不一次性引入所有扩展能力
- 优先增强现有记忆体系，不替换 `MemoryManager / RetrievalEngine / MemoryType`
- `Scheduler` 是默认主入口，人工入口只作为补充触发方式
- 每一阶段都必须可测试、可回滚、可单独交付

## 2. 总体阶段

本次开发按 3 个主阶段推进：

1. `Phase 1`：打通最小自动闭环
2. `Phase 2`：接入评估与增强记忆反馈
3. `Phase 3`：低风险自动优化

每个阶段都包含：

- 开发任务
- 代码改动点
- 测试任务
- 验收标准
- 回滚策略

## 3. Phase 1：打通最小自动闭环

### 3.1 阶段目标

- 把 `self_check` 和 `failure_analysis` 的结果统一转成 `LearningCase`
- 把 `LearningCase` 落到独立 learning SQLite
- 把高价值失败经验和成功经验通过现有 `MemoryManager` 回写到长期记忆
- 让 `Scheduler` 自动定时执行 ingest 和 review
- 保持现有 `/selfcheck`、`system:daily_selfcheck`、`system:daily_memory` 等入口兼容

### 3.2 本阶段不做

- 不新增公开 API
- 不新增 setup-center 页面
- 不新增 `MemoryType`
- 不改检索排序
- 不做自动 patch 执行
- 不做 dataset export

### 3.3 代码任务拆分

#### 任务 A：新增 learning 基础模块

新增目录：

```text
src/openakita/learning/
  __init__.py
  models.py
  store.py
  case_builder.py
  feedback_writer.py
  scheduler_hooks.py
```

实现要求：

- `models.py`：定义 `LearningCase`
- `store.py`：负责 `learning_cases` 和可选 `learning_runs` 的 SQLite 持久化
- `case_builder.py`：把 `CheckReport`、`FailureAnalysisResult`、用户纠正信息转成 `LearningCase`
- `feedback_writer.py`：把高价值 `LearningCase` 写回现有记忆系统
- `scheduler_hooks.py`：为 scheduler 提供 ingest/review 执行入口

建议完成顺序：

1. `models.py`
2. `store.py`
3. `case_builder.py`
4. `feedback_writer.py`
5. `scheduler_hooks.py`

#### 任务 B：改造 `self_check`

目标文件：

- [self_check.py](file:///root/projects/openakita/src/openakita/evolution/self_check.py)

必须完成：

- 补齐 `learn_from_check()` 的 TODO
- 新增 `_build_cases_from_check(report)`
- 新增 `_write_check_cases(cases)`
- 新增 `_generate_check_memory_feedback(cases)`

目标行为：

```text
run_daily_check()
 -> 生成 CheckReport
 -> learn_from_check(report)
 -> build LearningCase
 -> 写 learning store
 -> 满足阈值则写 ERROR / EXPERIENCE 到长期记忆
```

#### 任务 C：接入 `failure_analysis`

目标文件：

- [failure_analysis.py](file:///root/projects/openakita/src/openakita/evolution/failure_analysis.py)

必须完成：

- 新增 `to_learning_case()`
- 在任务失败收尾路径里调用该适配逻辑
- 将失败根因、上下文、摘要写入 `learning_cases`

#### 任务 D：接入 scheduler

目标文件：

- [agent.py](file:///root/projects/openakita/src/openakita/core/agent.py)
- [executor.py](file:///root/projects/openakita/src/openakita/scheduler/executor.py)

新增系统任务：

- `system:hourly_learning_ingest`
- `system:daily_learning_review`

建议调度：

- `hourly_learning_ingest`：每小时运行一次
- `daily_learning_review`：每天清晨运行一次

目标行为：

- ingest：收集自检、失败分析、用户纠正等新证据，构建 `LearningCase`
- review：按照阈值筛选高价值案例，写回长期记忆，输出汇总日志

#### 任务 E：接入现有记忆系统

目标文件：

- [manager.py](file:///root/projects/openakita/src/openakita/memory/manager.py)

注意事项：

- 第一阶段不新增 `MemoryType`
- 失败经验写 `ERROR`
- 成功套路写 `EXPERIENCE`
- `playbook / anti_pattern` 仅通过 `tags` 或 `metadata` 标记

写入规则：

- 高频失败：`ERROR` + `anti_pattern`
- 成功套路：`EXPERIENCE` + `playbook`
- 用户显式纠正：`RULE` 或 `PREFERENCE`

### 3.4 Phase 1 测试任务

#### 单元测试

新增：

- `tests/unit/test_learning_case_builder.py`
- `tests/unit/test_learning_store.py`
- `tests/unit/test_memory_feedback_writer.py`

覆盖点：

- `CheckReport -> LearningCase`
- `FailureAnalysisResult -> LearningCase`
- 重复 case 不重复写入
- 阈值不足时不写长期记忆
- 阈值满足时正确写入 `ERROR / EXPERIENCE`

#### 集成测试

新增或扩展：

- 自检失败 -> `learning_cases` -> Memory 写回
- scheduler 自动触发 ingest
- scheduler 自动触发 review

建议命名：

- `tests/integration/test_learning_selfcheck_flow.py`
- `tests/integration/test_learning_failure_analysis_flow.py`
- `tests/unit/test_scheduler_tasks.py`

#### 回归测试

准备最小数据集：

- 高频失败样本集
- 高频成功样本集

### 3.5 Phase 1 验收标准

- 自检失败可以写入 `learning_cases`
- 失败分析结果可以写入 `learning_cases`
- `Scheduler` 能自动执行 ingest / review
- 高价值失败经验能写入长期记忆
- 成功套路能写入长期经验
- 现有对话、记忆整理、自检入口不受破坏

### 3.6 Phase 1 回滚策略

- 通过配置关闭 learning 闭环：
  - `LEARNING_LOOP_ENABLED=false`
  - `LEARNING_INGEST_ENABLED=false`
- 禁用新增 scheduler 任务
- 保留 learning DB，但停止写入
- learning 写回失败不得影响原有 `self_check` 和记忆主流程

## 4. Phase 2：接入评估与增强记忆反馈

### 4.1 阶段目标

- 让 `DailyEvaluator` 正式进入 scheduler 主流程
- 让评估结果也能写入 `LearningCase`
- 引入最小记忆命中统计，为后续 credit 体系做准备
- 将命中统计接入 `daily_evaluation / daily_learning_review` 摘要
- 增加最小 `credit` 观测骨架，但不引入自动调参

### 4.2 代码任务拆分

#### 任务 F：接入 `DailyEvaluator`

目标文件：

- [optimizer.py](file:///root/projects/openakita/src/openakita/evaluation/optimizer.py)
- [executor.py](file:///root/projects/openakita/src/openakita/scheduler/executor.py)

必须完成：

- 为评估结果新增 `to_learning_case()` 适配逻辑
- 增加 `system:daily_evaluation`
- 把评估输出落到 `learning_cases`

#### 任务 G：增加最小记忆命中统计

目标范围：

- `learning` 模块内部
- 先不改现有检索排序

必须完成：

- 记录哪些案例最终写入了长期记忆
- 记录这些记忆在后续周期中是否再次被命中
- 输出最小统计报表，供后续 credit 体系使用

补充完成：

- 将 hit 统计写入 `learning_runs.summary_json`
- 让 `daily_learning_review` 可以读取 hit 摘要

#### 任务 H：增加最小 credit 观测骨架

目标范围：

- `learning` 模块内部
- 只做观测与报表，不改变现有排序或写回决策

必须完成：

- 为命中对象增加 `helpful / neutral / harmful` 最小累积统计
- 让 `daily_evaluation` 根据聚合结果写入 credit 观测
- 让 `daily_learning_review` 能看到最近一轮 credit 摘要

本阶段先不做：

- 自动升权/降权
- 基于 credit 的策略调优
- 基于 credit 的自动动作筛选

本阶段先不做：

- `retrieval_boost`
- 自动降权/升权
- 修改 `RetrievalEngine` 排序

### 4.3 Phase 2 测试任务

#### 单元测试

- `DailyEvaluator` 结果转 `LearningCase`
- 评估失败与成功样本能正确分类
- 最小命中统计可累计
- 命中统计可进入评估摘要与 review 摘要
- 最小 credit 统计可累计与汇总

#### 集成测试

- `system:daily_evaluation` 能自动运行
- 评估结果能进入 `learning_cases`
- `daily_evaluation / daily_learning_review` 能产出最小 hit / credit 报表

### 4.4 Phase 2 验收标准

- `DailyEvaluator` 已进入 scheduler 主流程
- 评估结果可以沉淀为 `LearningCase`
- 自检、失败分析、评估三类来源已统一进入 learning store
- 最小命中统计可用于后续分析
- 最小 credit 观测已可用于后续策略设计

### 4.5 Phase 2 回滚策略

- 单独关闭 `system:daily_evaluation`
- 保留 Phase 1 能力继续工作
- 评估接入失败不得影响自检和记忆主流程

## 5. Phase 3：低风险自动优化

### 5.1 阶段目标

- 在不触碰核心目录自动修改的前提下，实现低风险自动优化
- 只允许极小范围自动动作
- 所有动作必须可验证、可审计、可回滚
- 默认从 shadow / dry-run 起步，不直接开启自动生效

### 5.2 本阶段允许的自动目标

- `prompt/overrides/`
- `skills/`
- 工具提示词片段
- 少量受控参数

### 5.3 本阶段禁止的自动目标

- `src/openakita/core/`
- `src/openakita/memory/`
- `src/openakita/llm/`
- `src/openakita/agents/`
- `src/openakita/scheduler/`

### 5.4 代码任务拆分

#### 任务 I：新增最小 planner

职责：

- 把高价值 `LearningCase` 转成低风险候选动作
- 只支持：
  - `prompt_patch`
  - `tool_hint_patch`
  - `skill_backlog_create`

#### 任务 J：新增最小 executor

职责：

- 在白名单路径内执行低风险动作
- 自动创建 checkpoint
- 自动记录 action log

#### 任务 K：新增最小 verifier

职责：

- 对变更做最小验证
- 支持：
  - `smoke`
  - `replay`

### 5.5 Phase 3 测试任务

#### 单元测试

- `LearningCase -> Action`
- Action 白名单判断
- verifier 正确判定成功/失败

#### 集成测试

- prompt patch 自动执行后可以通过 smoke 验证
- skill 提示更新后可通过最小 replay
- 验证失败会自动回滚
- dry-run / shadow 模式下不会直接污染正式配置

### 5.6 Phase 3 验收标准

- 至少支持 2 类低风险自动动作
- 至少支持 2 类最小验证
- 所有自动动作都有 action record
- 验证失败能够自动回滚

### 5.7 Phase 3 回滚策略

- 关闭 `LEARNING_ENABLE_LOW_RISK_AUTOFIX`
- 停用自动动作执行，仅保留 case 沉淀
- 对已应用动作执行 checkpoint 回滚

## 6. 统一开发顺序

建议严格按下面顺序推进，不要跳步：

1. 新增 `learning/models.py`
2. 新增 `learning/store.py`
3. 新增 `learning/case_builder.py`
4. 新增 `learning/feedback_writer.py`
5. 改造 `self_check.learn_from_check()`
6. 改造 `failure_analysis.py`
7. 新增 `system:hourly_learning_ingest`
8. 新增 `system:daily_learning_review`
9. 补齐 Phase 1 单元测试与集成测试
10. 跑通 Phase 1 验收
11. 接入 `DailyEvaluator`
12. 加入最小命中统计
13. 加入评估摘要与最小 credit 观测
14. 补齐 Phase 2 测试与验收
15. 再开始低风险自动优化

## 7. 每一步的执行要求

每完成一个任务，都必须执行以下动作：

1. 写代码
2. 运行对应单元测试
3. 运行对应集成测试
4. 检查日志和数据库写入结果
5. 检查是否破坏原有入口
6. 更新开发记录

建议执行节奏：

- 一次只推进一个任务包
- 一个任务包最多修改一组相邻模块
- 每完成一个任务包就提交一次阶段性测试结果

## 8. 建议的任务包划分

为了方便后续逐项推进，建议按下面 8 个任务包开发：

### 包 1：Learning 基础数据层

- `models.py`
- `store.py`
- 对应单元测试

### 包 2：Case 构建层

- `case_builder.py`
- `failure_analysis.py` 适配
- 对应单元测试

### 包 3：SelfCheck 学习接入

- `self_check.py`
- `feedback_writer.py`
- 自检链路集成测试

### 包 4：Scheduler 接入

- `agent.py`
- `scheduler/executor.py`
- scheduler 集成测试

### 包 5：评估接入

- `evaluation/optimizer.py`
- `system:daily_evaluation`
- 评估链路测试

### 包 6：记忆反馈统计

- 最小命中统计
- 报表输出

### 包 7：低风险 planner/executor

- `planner.py`
- `executor.py`
- 白名单执行测试

### 包 8：最小 verifier 与回滚

- `verifier.py`
- checkpoint / rollback 测试

## 9. 测试清单

开发过程中至少要持续执行以下测试命令：

```bash
pytest tests/unit/test_learning_case_builder.py
pytest tests/unit/test_learning_store.py
pytest tests/unit/test_memory_feedback_writer.py
pytest tests/unit/test_scheduler_tasks.py
pytest tests/integration/test_learning_selfcheck_flow.py
pytest tests/integration/test_learning_failure_analysis_flow.py
```

阶段完成后建议执行：

```bash
pytest -k "learning or selfcheck or memory"
pytest tests/unit/
pytest tests/integration/
```

如修改了 scheduler、memory、自检相关主链，建议补做：

```bash
pytest -k "scheduler or memory or selfcheck"
```

## 10. 开发记录要求

建议新增一份阶段开发记录，持续记录：

- 当前阶段
- 当前任务包
- 已完成任务
- 待处理问题
- 测试结果
- 是否可回滚

建议文件：

- `docs/learning-loop-progress.md`

该文件不是第一阶段必须项，但强烈建议同步维护。

## 11. 最终交付顺序

最终建议按以下顺序交付：

1. `Phase 1` 代码 + 测试 + 验收记录
2. `Phase 2` 代码 + 测试 + 验收记录
3. `Phase 3` 代码 + 测试 + 验收记录

不要把三阶段代码混在同一批改动里完成。

## 12. 一句话总结

后续研发应当遵循这条主线：

> 先打通 `LearningCase -> Store -> MemoryFeedbackWriter -> Scheduler Review` 的最小自动闭环，再接入评估，最后才做低风险自动优化，并且每一步都必须有测试、验收和回滚方案。

## 13. 下一阶段建议清单

在 `Phase 3` 最小自动优化链交付后，建议下一阶段按下面顺序推进。

### 13.1 方向一：稳定性与治理

建议优先处理：

1. 连续多日 scheduler 无人值守运行观察
2. `learning_case_retention_days` 对应的实际清理任务
3. learning run 的周期性健康检查 / 异常排查指引
4. 回滚、停用、恢复操作文档

原因：

- 先确保自动链能长期稳定运行
- 先把故障处理和运维收口补齐
- 再进入更复杂的自动推广能力

### 13.2 方向二：增强型 promoter

建议新增独立 promoter 层，而不是继续把所有治理逻辑堆进最小 `promote`：

- 增加更细粒度的准入条件
- 增加 canary / 分批推广 / 自动停机策略
- 增加策略化的 promote 决策记录
- 保持对核心目录的禁止自动改写边界

### 13.3 方向三：可观测性增强

建议逐步补齐：

- `shadow / promote / verifier` 的日报 / 周报摘要
- 趋势指标：
  - shadow 命中率
  - promote 成功率
  - verifier 回滚率
- 后续视需要接 dashboard 或导出报表

### 13.4 方向四：扩展能力

在前面三类工作完成后，再考虑：

- dataset exporter
- 手工补跑入口
- 公开 API
- setup-center 页面

### 13.5 推荐推进顺序

建议下一阶段拆成以下 4 个任务包：

#### 包 9：稳定性与保留策略

- retention 清理任务
- 连续运行观察
- 健康检查与异常排查

#### 包 10：增强型 promoter / canary

- promoter 策略层
- canary / 分批推广
- 自动停止与恢复条件

#### 包 11：观测与报表增强

- 日报 / 周报摘要
- 趋势指标
- 运行报表

#### 包 12：外部入口与扩展面

- 手工补跑入口
- API
- UI
- exporter
