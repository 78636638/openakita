# Task Guardian 任务守护技能

## 功能说明

Task Guardian 用于解决 AI 任务执行闭环问题。当用户说"稍等"、"继续"等词时，如果没有实际调用工具就停止响应，任务就会进入"pending_trigger"状态，等待定时器自动扫描并重新触发执行。

## 核心能力

| 能力 | 说明 |
|------|------|
| 待办队列 | 持久化存储任务状态 |
| 定时扫描 | 每5分钟自动检查超时任务 |
| 超时重试 | 超时任务自动重新触发 |
| 输出验证 | 验证承诺的文件是否真实存在 |
| 失败告警 | 超过最大重试次数自动标记失败 |

## 与现有系统的关系

- **复用 `create_todo` 等工具**：Task Guardian 不替代现有 Todo 工具，而是补充"自动重试"能力
- **数据存储**：任务状态存 `data/task_guardian_state.json`（可改为 PostgreSQL）
- **定时触发**：复用系统的 `schedule_task` 工具

## 与系统内建 Todo 的区别

| 方面 | 系统 Todo | Task Guardian |
|------|----------|--------------|
| 创建 | 手动调用 | 自动创建 |
| 超时重试 | 无 | 有 |
| 输出验证 | 无 | 有 |
| 定时扫描 | 无 | 有 |
| 失败告警 | 无 | 有 |

## 适用场景

✅ **适用**：
- 用户说"继续做XXX"，但AI没有实际执行
- 复杂任务需要多轮迭代
- 子 Agent 声称完成但文件不存在
- 需要后台自动执行的任务

❌ **不适用**：
- 简单一次性任务（说一句话就能完成）
- 用户明确说"不用了"
- 实时性要求很高的任务

## 配置项

在 `.env` 中添加：

```bash
# Task Guardian 配置
GUARDIAN_ENABLED=true
GUARDIAN_SCAN_INTERVAL=5        # 扫描间隔（分钟）
GUARDIAN_RETRY_TIMEOUT=10       # 超时时间（分钟）
GUARDIAN_MAX_RETRIES=3          # 最大重试次数
```

## 使用示例

### 1. 自动启用守护

```
用户: 继续V4优化
↓
AI识别：需要多轮执行，创建待办 + 定时器
↓
TaskGuardian.add_pending_task(
    task_id="v4_optimize_001",
    description="优化CLUSTER_DESIGN_V4.md的sharefolder设计",
    promised_files=["/root/projects/openakita/docs/CLUSTER_DESIGN_V4.md"]
)
↓
schedule_task(guardian_scan_task, trigger_type="interval", interval_minutes=5)
```

### 2. 定时扫描（Guardian Agent）

每5分钟自动触发，检查待办队列：

```python
guardian = TaskGuardian()
overdue_tasks = guardian.check_pending_tasks()

for task in overdue_tasks:
    if task["retry_count"] < MAX_RETRIES:
        # 重新触发执行
        notify_user(f"任务 {task['id']} 超时，正在重新执行...")
        re_delegate_task(task)
    else:
        # 超过最大重试次数，标记失败
        guardian.fail_task(task['id'], "超过最大重试次数")
        notify_user(f"任务 {task['id']} 失败，需要人工处理")
```

### 3. 手动查询状态

```
用户: 查看待办任务
↓
TaskGuardian.list_tasks()
↓
返回所有 pending_trigger 状态的任务
```

## 状态流转

```
用户说"继续XXX"
    ↓
add_pending_task() → pending_trigger
    ↓
Guardian 定时扫描
    ↓
    ├── 超时 → 重试（retry_count++）
    ├── 完成 → 验证文件 → completed
    └── 超时超限 → failed
```

## 文件结构

```
skills/external/task-guardian/
├── SKILL.md                      ← 技能定义
├── README.md                      ← 本文件
└── scripts/
    └── check_pending_tasks.py     ← 核心脚本
```