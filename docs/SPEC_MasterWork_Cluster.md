# OpenAkita Master-Work 分布式集群管理系统

## 设计方案 v4.0

**项目**：`/root/projects/openakita/`
**版本**：基于 OpenAkita v1.27.13
**日期**：2026-05-28
**状态**：终稿，待实施

---

## 一、背景与目标

### 1.1 用户需求

用户（Waylin）需要一套分布式 Multi-Agent 集群管理系统：

| 角色 | 职责 |
|------|------|
| **Master** | 统一管理任务、分发、汇总结果、Review 交付物 |
| **Work 分身集群** | PM、需求分析师、系统设计师、开发工程师、测试工程师 |

**任务链**：Master 拆分 → Work 执行 → 结果汇总 → Master Review → 测试 Work 验收 → 反馈闭环

### 1.2 核心需求确认

| 问题 | 选择 |
|------|------|
| **注册中心** | Redis（共享注册中心） |
| **消息队列** | RabbitMQ（统一通信） |
| **记忆汇总** | 各 Work 独立记忆，任务完成后经验同步 Master |
| **规模支持** | 20+ 大型，动态扩展 |
| **Master 容灾** | 人工切换，Work 继续执行任务 |
| **配置驱动** | .env 动态配置 + 角色描述 |
| **共享文件** | `sharefolder/{node_id}/` 本地文件系统 |
| **Work 通信** | 不支持直接通信，全部通过 RabbitMQ |
| **Master 挂了** | 任务继续执行，恢复后补全流程 |

---

## 二、系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      Master Node                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ TaskPool │ │ResultHub │ │ReviewMgr │ │ WorkRegistry │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  ClusterAPI (HTTP)  +  WebSocketServer (Push)     │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
         ▲                                            ▲
         │  HTTP POST/WS                       RabbitMQ │
         │  (任务下发)                      (结果上报)  │
┌────────┴──────────────────────────────────────────────────┐
│                      RabbitMQ Cluster                        │
│  Exchanges: cluster.direct                                    │
│  Queues: task.{node_id}, result.{node_id}, broadcast       │
└─────────────────────────────────────────────────────────────┘
         ▲                                            ▲
         │  RabbitMQ                     RabbitMQ    │
┌────────┴───────────────┐          ┌────────────────┴──────┐
│      Work Node 1        │          │       Work Node N      │
│  ┌─────────────────┐   │          │  ┌─────────────────┐   │
│  │  WorkClient     │   │          │  │  WorkClient     │   │
│  │  RoleExecutor   │   │          │  │  RoleExecutor   │   │
│  └─────────────────┘   │          │  └─────────────────┘   │
└─────────────────────────┘          └───────────────────────┘
```

### 2.2 核心组件说明

| 组件 | 说明 |
|------|------|
| **Master** | 任务调度中心，统一管理集群 |
| **Work** | 任务执行节点，按角色执行任务 |
| **Redis** | 注册中心存储、心跳存储、集群列表 |
| **RabbitMQ** | 统一通信（任务下发、结果上报、心跳广播） |

### 2.3 同一套代码 + 差异化配置

```
同一套代码 + .env 差异化配置
         │
         ▼
┌─────────────────────────────────────┐
│         .env 节点配置               │
│  CLUSTER_NODE_ID, CLUSTER_ROLE,    │
│  CLUSTER_NODE_DESC, ...            │
└─────────────────────────────────────┘
         │
    启动时读取
         │
         ▼
┌─────────────────────────────────────┐
│     自动注册到 Redis 注册中心       │
│  + 心跳保活 + 集群列表同步         │
└─────────────────────────────────────┘
         │
    角色分支
    ┌───┴───┐
    │       │
 Master   Work
 角色     角色
```

---

## 三、.env 配置项设计

### 3.1 集群基础配置

```bash
# ========== 集群配置 ==========
# 节点身份标识（全局唯一）
CLUSTER_NODE_ID=node_001

# 节点角色（master / work）
CLUSTER_ROLE=master

# 节点描述（用于 LLM 智能分派决策）
CLUSTER_NODE_DESC="PM项目经理，负责任务规划、进度协调、需求优先级排序"

# Master 节点地址（Work 必填，Master 可选）
CLUSTER_MASTER_URL=http://192.168.1.100:18900

# Redis 注册中心地址
CLUSTER_REDIS_HOST=192.168.1.100
CLUSTER_REDIS_PORT=6379
CLUSTER_REDIS_PASSWORD=
CLUSTER_REDIS_DB=0

# RabbitMQ 地址
CLUSTER_RABBITMQ_HOST=192.168.1.100
CLUSTER_RABBITMQ_PORT=5672
CLUSTER_RABBITMQ_USER=guest
CLUSTER_RABBITMQ_PASSWORD=guest
CLUSTER_RABBITMQ_VHOST=/

# 心跳间隔（秒）
CLUSTER_HEARTBEAT_INTERVAL=10

# 共享文件夹路径
CLUSTER_SHAREFOLDER=/root/openakita-all/sharefolder
```

### 3.2 Work 节点附加配置

```bash
# Work 节点必填
CLUSTER_ROLE=work
CLUSTER_MASTER_URL=http://192.168.1.100:18900

# Work 角色类型（可选，辅助描述）
CLUSTER_WORK_TYPE=developer

# 角色描述（LLM 用于理解此 Work 的能力）
CLUSTER_NODE_DESC="开发工程师，负责代码实现、功能开发、模块设计"
```

---

## 四、Redis 数据结构设计

### 4.1 注册中心

| Key | 类型 | 说明 | TTL |
|-----|------|------|-----|
| `cluster:nodes` | Hash | 所有节点信息 | 无 |
| `cluster:nodes:{node_id}` | Hash | 单个节点详情 | 无 |
| `cluster:heartbeat:{node_id}` | String | 心跳时间戳 | 30s |
| `cluster:master` | String | 当前 Master 节点 ID | 无 |

### 4.2 节点信息 Hash 结构

```
cluster:nodes:{node_id}
  - node_id: string
  - role: "master" | "work"
  - node_type: string  # 如 developer, pm, tester 等
  - description: string  # 角色描述
  - master_url: string  # Master 访问地址
  - redis_host: string
  - redis_port: string
  - heartbeat_interval: int
  - sharefolder: string
  - status: "online" | "offline"
  - registered_at: timestamp
  - last_heartbeat: timestamp
```

### 4.3 任务池

| Key | 类型 | 说明 |
|-----|------|------|
| `cluster:tasks` | SortedSet | 所有任务（按创建时间） |
| `cluster:tasks:pending` | List | 待认领任务 |
| `cluster:tasks:assigned` | Hash | 已分配任务 |
| `cluster:tasks:completed` | List | 已完成任务 |
| `cluster:tasks:failed` | List | 失败任务 |

---

## 五、RabbitMQ 队列设计

### 5.1 Exchange 配置

| Exchange | 类型 | 说明 |
|----------|------|------|
| `cluster.direct` | direct | 集群主交换机 |
| `cluster.broadcast` | fanout | 广播交换机 |

### 5.2 Queue 配置

| Queue | 绑定 | 说明 |
|-------|------|------|
| `task.{node_id}` | cluster.direct | 特定节点的任务队列 |
| `result.{node_id}` | cluster.direct | 特定节点的结果队列 |
| `cluster.heartbeat` | cluster.broadcast | 心跳广播队列 |
| `cluster.notifications` | cluster.broadcast | 通知广播队列 |

### 5.3 消息格式

```json
// 任务消息
{
  "msg_id": "uuid",
  "msg_type": "task",
  "task_id": "uuid",
  "task_type": "development",
  "payload": {
    "description": "开发用户登录模块",
    "requirements": ["..."],
    "parent_task_id": null
  },
  "priority": 1,
  "timeout": 3600,
  "created_at": "2026-05-28T20:00:00Z"
}

// 结果消息
{
  "msg_id": "uuid",
  "msg_type": "result",
  "task_id": "uuid",
  "node_id": "node_002",
  "status": "completed",
  "payload": {
    "deliverables": ["..."],
    "summary": "登录模块开发完成",
    "experience": "首次使用RabbitMQ实现分布式通信"
  },
  "completed_at": "2026-05-28T21:00:00Z"
}

// 心跳消息
{
  "msg_id": "uuid",
  "msg_type": "heartbeat",
  "node_id": "node_002",
  "status": "online",
  "timestamp": "2026-05-28T20:00:00Z"
}
```

---

## 六、共享文件系统设计

### 6.1 目录结构

```
sharefolder/
├── node_001/           # 各节点独立目录
│   ├── deliverables/  # 交付物（只写）
│   ├── cache/         # 缓存（只写）
│   └── temp/          # 临时文件（只写）
├── node_002/
│   └── ...
└── node_N/
```

### 6.2 权限控制

| 操作 | 权限 |
|------|------|
| 读取任意节点目录 | ✅ 所有节点 |
| 写入自己节点目录 | ✅ 仅自己 |
| 修改/删除其他节点目录 | ❌ 禁止 |
| 查看集群共享文件列表 | ✅ 所有节点 |

### 6.3 文件分享流程

```
1. Work A 完成交付物
2. 将文件放入 sharefolder/node_A/deliverables/
3. 通过 RabbitMQ 发送结果消息给 Master
4. Master 读取 sharefolder/node_A/deliverables/ 查看交付物
5. 其他 Work 可访问但不能修改
```

---

## 七、API 接口设计

### 7.1 Master 端 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/cluster/tasks` | 创建任务 |
| GET | `/api/cluster/tasks` | 获取任务列表 |
| GET | `/api/cluster/tasks/{id}` | 获取任务详情 |
| PUT | `/api/cluster/tasks/{id}/assign` | 分配任务给 Work |
| GET | `/api/cluster/nodes` | 获取集群节点列表 |
| GET | `/api/cluster/nodes/{id}` | 获取节点详情 |
| POST | `/api/cluster/nodes/{id}/heartbeat` | 节点心跳 |
| GET | `/api/cluster/results` | 获取已完成结果 |
| POST | `/api/cluster/files/upload` | 上传共享文件 |
| GET | `/api/cluster/files/{node_id}/{path}` | 读取共享文件 |

### 7.2 Work 端 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/work/tasks/pending` | 获取待认领任务 |
| POST | `/api/work/tasks/{id}/claim` | 认领任务 |
| POST | `/api/work/tasks/{id}/progress` | 更新进度 |
| POST | `/api/work/tasks/{id}/complete` | 完成任务 |
| POST | `/api/work/tasks/{id}/fail` | 任务失败 |
| POST | `/api/work/results` | 上报结果 |
| POST | `/api/work/heartbeat` | 发送心跳 |
| GET | `/api/work/cluster/nodes` | 获取集群节点列表 |
| GET | `/api/work/cluster/nodes/{id}` | 获取其他节点信息 |

---

## 八、目录结构设计

```
src/openakita/cluster/
├── __init__.py
├── config.py              # 集群配置加载
├── exceptions.py         # 异常定义
├── models.py              # 数据模型
│
├── master/               # Master 端模块
│   ├── __init__.py
│   ├── task_pool.py      # 任务池管理
│   ├── task_dispatcher.py# 任务分发
│   ├── result_hub.py     # 结果汇总
│   ├── review_manager.py # Review 管理
│   ├── work_registry.py # Work 注册管理
│   └── master_api.py     # Master HTTP API
│
├── work/                  # Work 端模块
│   ├── __init__.py
│   ├── work_client.py    # Work 客户端
│   ├── task_receiver.py  # 任务接收
│   ├── role_executor.py  # 角色执行器
│   ├── result_reporter.py# 结果上报
│   └── work_api.py       # Work HTTP API
│
├── storage/              # 存储层
│   ├── __init__.py
│   ├── redis_client.py  # Redis 客户端
│   ├── task_store.py     # 任务存储
│   └── node_store.py     # 节点存储
│
├── messaging/           # 消息通信层
│   ├── __init__.py
│   ├── rabbitmq_client.py# RabbitMQ 客户端
│   ├── task_publisher.py # 任务发布
│   ├── result_subscriber.py# 结果订阅
│   └── heartbeat_broadcaster.py# 心跳广播
│
├── sharefolder/          # 共享文件夹
│   ├── __init__.py
│   ├── folder_manager.py # 文件夹管理
│   └── file_access.py    # 文件访问控制
│
└── api/                  # API 路由
    ├── __init__.py
    ├── master_routes.py # Master 路由
    └── work_routes.py   # Work 路由
```

---

## 九、模块详细设计

### 9.1 Master 模块

#### TaskPool（任务池）
- 创建任务
- 拆分大任务为子任务
- 维护任务状态（pending/assigned/running/completed/failed）

#### TaskDispatcher（任务分发）
- 根据 Work 描述选择最合适的 Work
- 通过 RabbitMQ 发送任务消息
- 处理任务超时

#### ResultHub（结果汇总）
- 接收 Work 上报的结果
- 存储交付物到共享文件夹
- 汇总经验同步给 Master 记忆

#### ReviewManager（Review 管理）
- 审查交付物质量
- 决定通过/打回/重新分配

#### WorkRegistry（Work 注册管理）
- 处理 Work 注册请求
- 维护心跳健康检测
- 标记 Work 状态

### 9.2 Work 模块

#### WorkClient（Work 客户端）
- 启动时从 .env 读取配置
- 注册到 Redis 注册中心
- 连接 RabbitMQ 监听任务队列

#### TaskReceiver（任务接收）
- 监听 RabbitMQ task.{node_id} 队列
- 解析任务消息
- 调用 RoleExecutor 执行

#### RoleExecutor（角色执行器）
- 按角色执行任务
- 支持 PM/需求/设计/开发/测试 角色
- 调用 LLM 进行推理

#### ResultReporter（结果上报）
- 将执行结果写入共享文件夹
- 通过 RabbitMQ 上报结果
- 同步经验给 Master

### 9.3 Storage 模块

#### RedisClient
- 连接 Redis
- 提供注册中心 CRUD
- 提供心跳存储

#### TaskStore
- 任务 CRUD
- 任务状态管理
- 任务历史记录

#### NodeStore
- 节点 CRUD
- 节点状态管理

### 9.4 Messaging 模块

#### RabbitMQClient
- 连接 RabbitMQ
- 管理 exchanges 和 queues
- 提供发布/订阅能力

#### TaskPublisher
- 发布任务到特定 Work 队列

#### ResultSubscriber
- 订阅结果队列
- 处理结果消息

#### HeartbeatBroadcaster
- 定期发送心跳
- 监听其他节点心跳

### 9.5 ShareFolder 模块

#### FolderManager
- 创建节点目录
- 管理权限
- 清理过期文件

#### FileAccess
- 读取共享文件
- 写入本节点文件
- 权限校验

---

## 十、实施计划

### Phase 1：核心链路（2-3 天）

| 任务 | 说明 |
|------|------|
| 集群配置加载 | .env 读取、节点初始化 |
| Redis 注册中心 | 节点注册、心跳、心跳检测 |
| RabbitMQ 连接 | 基本消息收发 |
| 任务 CRUD | 任务创建、分配、状态管理 |
| 简单任务分发 | Master → Work 单向分发 |

### Phase 2：角色 Work + 闭环（2 天）

| 任务 | 说明 |
|------|------|
| 角色执行器 | 支持 5 类角色执行 |
| 结果上报 | Work → Master 结果上报 |
| 经验同步 | 任务完成后经验同步 Master |
| Review 管理 | Master Review 流程 |
| 任务链闭环 | 完整链路测试 |

### Phase 3：高可用 + 安全（3-5 天）

| 任务 | 说明 |
|------|------|
| JWT 认证 | Token 升级为 JWT |
| WebSocket | 实时任务推送 |
| Master HA | 主备切换（人工） |
| 监控告警 | 任务超时、节点掉线告警 |
| 性能优化 | 并发能力压测 |

---

## 十一、FAQ

### Q1: Master 挂了怎么办？
A: Work 继续执行当前任务。Master 恢复后，Work 上报结果，完成后续流程。

### Q2: Work 挂了怎么办？
A: Master 检测到心跳超时后，将任务重新分配给其他 Work。

### Q3: RabbitMQ 挂了怎么办？
A: 任务暂时无法下发，Work 进入等待状态。RabbitMQ 恢复后自动重连。

### Q4: 如何新增 Work 节点？
A: 1. 在新机器部署同一套代码
   2. 配置 .env（CLUSTER_ROLE=work）
   3. 启动后自动注册到 Redis
   4. Master 可立即分发任务

### Q5: 共享文件夹如何跨机器共享？
A: 当前设计为本地文件系统。如需跨机器，可通过 NFS 挂载相同路径。

---

## 十二、待确认事项

在开发前需要确认以下事项：

| 问题 | 选项 |
|------|------|
| RabbitMQ 部署方式 | Docker / 直接安装 / 云服务？ |
| Redis 部署方式 | Docker / 直接安装 / 云服务？ |
| 共享文件夹跨机器方案 | NFS / 其他？ |
| Master API 端口 | 默认 18900？ |
| Work 节点数量 | 先从 3 个开始？ |