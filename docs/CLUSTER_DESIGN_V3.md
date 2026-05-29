# OpenAkita Master-Work 集群架构设计方案（V3）

> 版本: V3  
> 日期: 2025-05-28  
> 项目: `/root/projects/openakita/`  
> 状态: 新增节点描述信息 + 共享目录机制

---

## 审查摘要

本设计基于 **CLUSTER_DESIGN_FINAL.md**（已审查版），针对以下新增需求进行扩展：

| 新增模块 | 说明 |
|----------|------|
| 节点角色动态配置 | .env 增加描述/能力/专长字段，注册时携带描述信息 |
| 本地配置文件生成 | 启动时生成 cluster_nodes.json，缓存节点描述 |
| Master LLM 上下文增强 | Master 对话时加载所有 Work 节点描述到上下文 |
| 共享目录机制 | sharefolder/{node_id}/ 子目录隔离，读写权限控制 |

---

## 一、设计目标与范围

### 1.1 核心需求（V3 新增项）

| # | 需求 | 说明 |
|---|------|------|
| 1 | `.env` 配置文件 | 身份标识 + 集群机器列表 + **角色描述/能力/专长** |
| 2 | 自动注册 | 启动时注册到注册中心，**携带描述信息**，获取集群列表 |
| 3 | 定时心跳 | 节点定期上报存活状态 |
| 4 | Master 统一调度 | Master 负责任务分发、结果汇总、记忆汇聚 |
| 5 | Work 独立执行 | Work 接收任务、执行中可向 Master 或同伴求助、完成后反馈 |
| 6 | 源码共用 | 所有机器运行同一套代码，通过 `.env` 配置区分角色 |
| 7 | Master Failover | Master 故障时自动选主或任务恢复 |
| 8 | 多组织隔离 | 支持多租户/多组织隔离运行 |
| 9 | **节点描述信息** | 注册时携带角色描述、能力、专长，Master 可智能委派 |
| 10 | **共享目录机制** | sharefolder/{node_id}/ 目录隔离，节点间共享文件 |

### 1.2 与现有 `orgs/` 模块的关系

**⚠️ 重要说明**：现有 `orgs/` 模块是**单进程**架构，所有组件（OrgRuntime、OrgMessenger、OrgBlackboard）都是内存级通信，**无法直接跨机器复用**。

```
现有 orgs/ 架构（单进程）:
OrgRuntime (单例)
  ├── OrgMessenger (asyncio.PriorityQueue - 内存队列)
  ├── OrgBlackboard (本地文件存储)
  ├── OrgInbox (defaultdict - 内存字典)
  └── OrgHeartbeat (asyncio.create_task - 本地定时器)
```

**设计策略**：
- 复用 `orgs/` 的**数据模型**（Organization, OrgNode, OrgMessage, MsgType, TaskStatus 等）
- **不修改**现有 `orgs/` 模块，保持其单进程用途
- 在 `cluster/` 目录下实现**全新的分布式运行时**，基于 `orgs` 数据模型但重新实现通信层

---

## 二、系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Master Node                                │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐       │
│  │ MasterAgent  │   │ClusterMgr   │   │  TaskAggregator      │       │
│  │ (调度+汇总)  │◄──│(注册中心)   │   │  (记忆汇聚+结果Review)│       │
│  └──────┬───────┘   └──────┬───────┘   └──────────┬───────────┘       │
│         │                  │                      │                    │
│         │         ┌───────┴───────┐              │                    │
│         │         │ Redis (主)     │              │                    │
│         │         │ PostgreSQL(备) │              │                    │
│         │         └───────┬───────┘              │                    │
└─────────┼─────────────────┼───────────────────────┼────────────────────┘
          │                 │                       │
          │     ┌───────────┴───────────┐           │
          │     │   Redis Sentinel      │           │
          │     │   (高可用 + 故障转移) │           │
          │     └───────────┬───────────┘           │
          │                 │                       │
          │     ┌───────────┴───────────┐           │
          │     │   PostgreSQL          │           │
          │     │   (任务持久化+备选存储)│           │
          │     └───────────┬───────────┘           │
          │                 │                       │
          │  ───────────────┼─────────────────────── │
          │   WebSocket     │                       │
          │                 │                       │
    ┌─────┴─────┐      ┌────┴─────┐          ┌──────┴──────┐
    │  Work-PM  │      │Work-Analyst│  ...   │  Work-Test  │
    │ (PM角色)  │      │(分析师)   │          │ (测试)      │
    │           │      │          │          │             │
    │LocalRuntime│     │LocalRuntime│        │LocalRuntime │
    │(OrgRuntime)│     │(OrgRuntime)│        │(OrgRuntime) │
    └───────────┘      └───────────┘          └─────────────┘

    共享存储（Redis 主 + PostgreSQL 备）
    ├── ClusterRegistry (Redis Hash + Set)
    ├── 任务队列 (PostgreSQL 持久化)
    ├── 消息队列 (Redis Pub/Sub + PostgreSQL 备份)
    ├── 共享记忆 (PostgreSQL 持久化)
    ├── 交付物 (S3/MinIO)
    └── 共享目录 (共享文件系统 - sharefolder/)

    共享目录 sharefolder/
    ├── master-001/          ← Master 节点目录
    ├── work-pm-001/         ← PM Work 节点目录
    ├── work-analyst-001/     ← 分析师节点目录
    └── ...
```

### 2.2 Master Failover 架构

```
                    ┌─────────────────┐
                    │   Master-1 (主) │
                    │   正常运行      │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
    ┌─────────────────┐ ┌─────────┐ ┌─────────┐
    │  任务状态快照   │ │  心跳    │ │  选主锁  │
    │  (每5分钟)      │ │  (10s)  │ │ (Redlock)│
    └────────┬────────┘ └────┬────┘ └────┬────┘
             │              │           │
             ▼              ▼           ▼
    ┌─────────────────┐ ┌─────────┐ ┌─────────┐
    │ PostgreSQL      │ │ Redis   │ │ Redis   │
    │ (持久化存储)    │ │         │ │         │
    └────────┬────────┘ └─────────┘ └─────────┘
             │              │           │
             │    ┌─────────┴─────────┐   │
             │    │  Redis Sentinel   │   │
             │    │  (主从自动切换)  │   │
             │    └─────────┬─────────┘   │
             │              │              │
             ▼              ▼              ▼
    ┌─────────────────┐ ┌─────────────────┐
    │  Master-2 (从)  │ │  Master-3 (从)  │
    │  等待选举       │ │  等待选举       │
    └─────────────────┘ └─────────────────┘
```

### 2.3 组件层次

| 层级 | 组件 | 职责 |
|------|------|------|
| **接入层** | `ClusterGateway` | WebSocket 网关、节点接入认证、连接数限制 |
| **管理层** | `ClusterRegistry` | 注册中心：节点注册、心跳 tracking、在线列表、**节点描述缓存** |
| **调度层** | `MasterScheduler` | 任务分发、依赖管理、负载均衡 |
| **执行层** | `WorkExecutor` | Work 节点：接收任务、执行、反馈 |
| **存储层** | `SharedStore` | Redis + PostgreSQL 双存储，S3 交付物 |
| **感知层** | `HeartbeatMonitor` | 心跳检测、故障感知、自动摘牌 |
| **通信层** | `MsgBus` | Redis Pub/Sub + PostgreSQL 备份 |
| **容错层** | `FailoverManager` | Master 选举、任务恢复、状态快照 |
| **共享目录层** | `ShareFolderManager` | 共享目录权限控制、文件共享 |

---

## 三、节点角色动态配置（.env）

### 3.1 新增配置字段

#### 3.1.1 角色描述字段（所有节点）

```bash
# === 角色描述信息（V3 新增）===
NODE_DESCRIPTION="我是项目经理，负责需求分析、任务拆解、项目进度跟踪"
NODE_CAPABILITIES="需求分析,架构设计,代码审查,测试评估"
NODE_SPECIALTIES="python,web开发,数据库设计"
```

**字段说明**：

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `NODE_DESCRIPTION` | 字符串 | 当前角色的定位、能力、技能等描述 | "我是项目经理，负责需求分析..." |
| `NODE_CAPABILITIES` | CSV 列表 | 能力列表（逗号分隔） | "需求分析,架构设计,代码审查" |
| `NODE_SPECIALTIES` | CSV 列表 | 技术专长列表（逗号分隔） | "python,web开发,数据库设计" |

#### 3.1.2 Master 节点完整配置（含描述）

```bash
# =============================================================
# OpenAkita Cluster - Master Node Configuration (V3)
# =============================================================

# === 节点身份 ===
NODE_ROLE=master
NODE_ID=master-001
NODE_NAME="OpenAkita Master"
NODE_DESCRIPTION="我是集群中枢大脑，负责统一调度管理、任务分发、结果汇总、记忆汇聚"
NODE_CAPABILITIES="任务调度,结果汇总,记忆汇聚,故障恢复,负载均衡"
NODE_SPECIALTIES="系统架构,性能优化,分布式系统"
CLUSTER_AUTH_TOKEN=changeme_master_token_abc123xyz
CLUSTER_ORG_ID=default_org

# === 网络配置 ===
CLUSTER_BIND_HOST=0.0.0.0
CLUSTER_BIND_PORT=8899
CLUSTER_ADVERTISE_HOST=192.168.1.100
CLUSTER_ADVERTISE_PORT=8899

# === 连接数限制（流控）===
MASTER_MAX_CONNECTIONS=100
MASTER_RATE_LIMIT_PER_SEC=1000
WORK_OUTBOUND_QUEUE_SIZE=100

# === 注册中心 - Redis 主 ===
REGISTRY_MODE=self-hosted
REGISTRY_BACKEND=redis
REDIS_HOST=192.168.1.100
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password_here
REDIS_DB=0
REDIS_TLS_ENABLED=false
REDIS_TLS_CERT_PATH=/path/to/redis.crt

# === 注册中心 - PostgreSQL 备选 ===
DB_FALLBACK_ENABLED=true
DB_HOST=192.168.1.100
DB_PORT=5432
DB_NAME=openakita_cluster
DB_USER=postgres
DB_PASSWORD=your_db_password_here
DB_SSL_MODE=prefer
DB_SSL_CERT_PATH=/path/to/db.crt

# === 心跳配置 ===
HEARTBEAT_INTERVAL_SEC=10
HEARTBEAT_TIMEOUT_SEC=40
HEARTBEAT_MISSING_THRESHOLD=40
HEARTBEAT_OFFLINE_THRESHOLD=60
HEARTBEAT_GONE_THRESHOLD=180

# === PostgreSQL 任务持久化 ===
DB_HOST=192.168.1.100
DB_PORT=5432
DB_NAME=openakita_cluster
DB_USER=postgres
DB_PASSWORD=your_db_password_here

# === 对象存储（S3/MinIO）===
STORAGE_TYPE=s3
STORAGE_ENDPOINT=http://192.168.1.100:9000
STORAGE_BUCKET=openakita-cluster
STORAGE_ACCESS_KEY=your_access_key_here
STORAGE_SECRET_KEY=your_secret_key_here
STORAGE_SSL_ENABLED=false

# === 集群成员（种子节点）===
CLUSTER_SEED_NODES=192.168.1.101:8899,192.168.1.102:8899,192.168.1.103:8899

# === Master 特定配置 ===
MASTER_TASK_AGGREGATION=enabled
MASTER_AUTO_TASK_DISPATCH=enabled
MASTER_SNAPSHOT_INTERVAL_SEC=300
MASTER_ELECTION_LOCK_TTL_SEC=30

# === LLM 上下文配置（V3 新增）===
MASTER_LLM_CONTEXT_LOAD_WORK_DESCRIPTIONS=true  # Master 对话时加载 Work 描述
MASTER_LLM_CONTEXT_MAX_WORKS=50                # 最多加载多少 Work 描述
CLUSTER_NODES_CACHE_PATH=./data/cluster_nodes.json  # 本地节点列表缓存路径

# === 共享目录配置（V3 新增）===
SHAREFOLDER_ENABLED=true
SHAREFOLDER_ROOT=./sharefolder/
SHAREFOLDER_NODE_DIR=${SHAREFOLDER_ROOT}/${NODE_ID}/
SHAREFOLDER_READ_OTHERS=true                     # 是否可读取其他节点目录
SHAREFOLDER_MAX_SIZE_GB=10                      # 单节点目录最大存储 GB

# === 日志 ===
LOG_LEVEL=INFO
LOG_DIR=./logs/cluster

# === 启动配置校验 ===
CONFIG_VALIDATION_STRICT=true
```

#### 3.1.3 Work 节点完整配置（含描述）

```bash
# =============================================================
# OpenAkita Cluster - Work Node Configuration (V3)
# =============================================================

# === 节点身份 ===
NODE_ROLE=work
NODE_ID=work-pm-001
NODE_NAME="PM-张三"
NODE_ROLE_TAG=pm
NODE_DESCRIPTION="我是项目经理，负责需求分析、任务拆解、项目进度跟踪"
NODE_CAPABILITIES="需求分析,项目规划,进度追踪,团队协调,风险管理"
NODE_SPECIALTIES="python,web开发,数据库设计,敏捷开发"
CLUSTER_AUTH_TOKEN=changeme_master_token_abc123xyz
CLUSTER_ORG_ID=default_org

# === 网络配置 ===
CLUSTER_BIND_HOST=0.0.0.0
CLUSTER_BIND_PORT=8899
CLUSTER_ADVERTISE_HOST=192.168.1.101
CLUSTER_ADVERTISE_PORT=8899

# === 指向 Master ===
MASTER_HOST=192.168.1.100
MASTER_PORT=8899

# === 重连配置 ===
WORK_RECONNECT_MAX_RETRIES=10
WORK_RECONNECT_BASE_BACKOFF_SEC=2
WORK_RECONNECT_MAX_BACKOFF_SEC=60

# === 注册中心 - Redis 主 ===
REGISTRY_MODE=self-hosted
REGISTRY_BACKEND=redis
REDIS_HOST=192.168.1.100
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password_here
REDIS_DB=0
REDIS_TLS_ENABLED=false

# === 注册中心 - PostgreSQL 备选 ===
DB_FALLBACK_ENABLED=true
DB_HOST=192.168.1.100
DB_PORT=5432
DB_NAME=openakita_cluster
DB_USER=postgres
DB_PASSWORD=your_db_password_here

# === 心跳配置 ===
HEARTBEAT_INTERVAL_SEC=10
HEARTBEAT_TIMEOUT_SEC=40

# === 数据库（本地缓存）===
DB_HOST=192.168.1.100
DB_PORT=5432
DB_NAME=openakita_cluster
DB_USER=postgres
DB_PASSWORD=your_db_password_here

# === 对象存储 ===
STORAGE_TYPE=s3
STORAGE_ENDPOINT=http://192.168.1.100:9000
STORAGE_BUCKET=openakita-cluster
STORAGE_ACCESS_KEY=your_access_key_here
STORAGE_SECRET_KEY=your_secret_key_here

# === 集群成员（种子节点）===
CLUSTER_SEED_NODES=192.168.1.100:8899

# === Work 特定配置 ===
WORK_MAX_CONCURRENT_TASKS=2
WORK_CAN_SEEK_HELP=true
WORK_HELP_REQUEST_TIMEOUT_SEC=300
WORK_LOCAL_RUNTIME_ENABLED=true

# === 共享目录配置（V3 新增）===
SHAREFOLDER_ENABLED=true
SHAREFOLDER_ROOT=./sharefolder/
SHAREFOLDER_NODE_DIR=${SHAREFOLDER_ROOT}/${NODE_ID}/
SHAREFOLDER_READ_OTHERS=true
SHAREFOLDER_MAX_SIZE_GB=10

# === 日志 ===
LOG_LEVEL=INFO
LOG_DIR=./logs/cluster

# === 启动配置校验 ===
CONFIG_VALIDATION_STRICT=true
```

### 3.2 各角色配置示例

| 角色 | NODE_ROLE_TAG | NODE_DESCRIPTION 示例 |
|------|--------------|----------------------|
| **PM** | `pm` | "我是项目经理，负责需求分析、任务拆解、项目进度跟踪" |
| **需求分析师** | `analyst` | "我是需求分析师，负责业务逻辑梳理、用例设计、需求评审" |
| **系统设计师** | `architect` | "我是系统架构师，负责系统架构设计、技术方案输出、代码审查" |
| **开发工程师** | `developer` | "我是后端开发工程师，负责代码实现、单元测试、API 开发" |
| **测试工程师** | `tester` | "我是测试工程师，负责测试计划、测试用例、缺陷跟踪" |

---

## 四、节点描述注册机制

### 4.1 ClusterNode 数据模型扩展

```python
# cluster/models.py

from dataclasses import dataclass, field
from typing import Optional

@dataclass
class ClusterNode:
    """集群节点（扩展包含描述信息）"""
    
    # === 基础身份 ===
    node_id: str
    node_name: str
    role: str                              # "master" | "work"
    role_tag: str | None                   # "pm" | "analyst" | "architect" | "developer" | "tester"
    
    # === 网络信息 ===
    advertise_host: str
    advertise_port: int
    bind_host: str = "0.0.0.0"
    bind_port: int = 8899
    
    # === 认证信息 ===
    auth_token: str
    org_id: str = "default_org"
    
    # === 描述信息（V3 新增）===
    description: str = ""                  # 角色定位、能力、技能等描述
    capabilities: list[str] = field(default_factory=list)  # 能力列表
    specialties: list[str] = field(default_factory=list)   # 技术专长列表
    
    # === 状态 ===
    status: str = "offline"                # "online" | "offline" | "draining"
    last_heartbeat: str | None = None
    version: str = "1.27.13"
    
    # === 元数据 ===
    created_at: str = ""
    updated_at: str = ""
    
    def to_dict(self) -> dict:
        """转换为字典（用于 JSON 序列化）"""
        return {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "role": self.role,
            "role_tag": self.role_tag,
            "advertise_host": self.advertise_host,
            "advertise_port": self.advertise_port,
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "auth_token": self.auth_token,  # 传输时不包含敏感信息（如需脱敏）
            "org_id": self.org_id,
            "description": self.description,      # V3 新增
            "capabilities": self.capabilities,   # V3 新增
            "specialties": self.specialties,     # V3 新增
            "status": self.status,
            "last_heartbeat": self.last_heartbeat,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ClusterNode":
        """从字典创建（用于反序列化）"""
        return cls(
            node_id=data["node_id"],
            node_name=data["node_name"],
            role=data["role"],
            role_tag=data.get("role_tag"),
            advertise_host=data["advertise_host"],
            advertise_port=data["advertise_port"],
            bind_host=data.get("bind_host", "0.0.0.0"),
            bind_port=data.get("bind_port", 8899),
            auth_token=data["auth_token"],
            org_id=data.get("org_id", "default_org"),
            description=data.get("description", ""),      # V3 新增
            capabilities=data.get("capabilities", []),   # V3 新增
            specialties=data.get("specialties", []),     # V3 新增
            status=data.get("status", "offline"),
            last_heartbeat=data.get("last_heartbeat"),
            version=data.get("version", "1.27.13"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
```

### 4.2 注册 Payload

```python
# cluster/registry.py

class ClusterRegistry:
    """分布式注册中心 - 支持 Redis 主 + PostgreSQL 备"""
    
    REGISTRY_KEY = "openakita:cluster:nodes"
    HEARTBEAT_KEY_PREFIX = "openakita:cluster:hb:"
    NODESET_KEY = "openakita:cluster:online_set"
    
    async def register(self, node: ClusterNode) -> bool:
        """注册节点 - 携带描述信息"""
        node.created_at = datetime.now().isoformat()
        node.updated_at = datetime.now().isoformat()
        node.status = "online"
        
        try:
            await self._redis_register(node)
            self._use_postgres = False
            return True
        except Exception as e:
            logger.warning(f"[Registry] Redis register failed: {e}, trying PostgreSQL...")
            if self._fallback_enabled:
                await self._pg_register(node)
                self._use_postgres = True
                return True
            raise
    
    async def _redis_register(self, node: ClusterNode) -> None:
        """Redis 注册（包含描述信息）"""
        key = f"{self.REGISTRY_KEY}:{node.node_id}"
        pipe = self._redis.pipeline()
        
        # 存储节点信息（包含描述）
        pipe.hset(self.REGISTRY_KEY, node.node_id, json.dumps(node.to_dict()))
        
        # 设置心跳 TTL
        pipe.setex(
            f"{self.HEARTBEAT_KEY_PREFIX}{node.node_id}",
            self._heartbeat_timeout,
            datetime.now().isoformat()
        )
        
        # 加入在线集合
        pipe.sadd(self.NODESET_KEY, node.node_id)
        
        # 存储节点描述索引（用于按能力查找，V3 新增）
        if node.capabilities:
            for cap in node.capabilities:
                cap_key = f"openakita:cluster:capability_index:{cap}"
                pipe.sadd(cap_key, node.node_id)
        
        await pipe.execute()
        logger.info(f"[Registry] Node {node.node_id} registered with capabilities: {node.capabilities}")
```

### 4.3 注册流程

```
Work 节点启动:
    1. 读取 .env → 确定 NODE_ROLE=work, MASTER_HOST=xxx
    2. 读取描述字段（NODE_DESCRIPTION, NODE_CAPABILITIES, NODE_SPECIALTIES）
    3. 校验 CLUSTER_AUTH_TOKEN（必须与 Master 一致）
    4. 尝试连接 Redis → 注册到 ClusterRegistry（携带描述信息）
    5. 如果 Redis 不可用 → 降级到 PostgreSQL 注册
    6. 从 ClusterRegistry 获取 Master 节点地址和所有 Work 节点描述
    7. 生成 cluster_nodes.json 本地配置文件
    8. 验证 Master 的 CLUSTER_AUTH_TOKEN
    9. 与 Master 建立 WebSocket 连接（带认证）
    10. 开始定时心跳（每 HEARTBEAT_INTERVAL_SEC 秒）

Master 节点启动:
    1. 读取 .env → 确定 NODE_ROLE=master, CLUSTER_AUTH_TOKEN
    2. 读取描述字段（NODE_DESCRIPTION, NODE_CAPABILITIES, NODE_SPECIALTIES）
    3. 配置校验（validate_config）
    4. 启动 WebSocket 服务器（端口 8899）
    5. 初始化 ClusterRegistry（Redis + PostgreSQL 备选）
    6. 启动 ClusterRegistry 心跳监控
    7. 尝试获取主 Master 锁（Redlock）
    8. 如果是主 Master → 恢复未完成任务（从 PostgreSQL）
    9. 获取集群中所有 Work 节点描述，加载到 LLM 上下文
    10. 等待 Work 连接
```

---

## 五、集群节点列表与本地配置

### 5.1 cluster_nodes.json 结构

```json
{
  "version": "1.27.13",
  "generated_at": "2025-05-28T12:00:00",
  "node_id": "master-001",
  "role": "master",
  "nodes": [
    {
      "node_id": "work-pm-001",
      "node_name": "PM-张三",
      "role": "work",
      "role_tag": "pm",
      "advertise_host": "192.168.1.101",
      "advertise_port": 8899,
      "status": "online",
      "description": "我是项目经理，负责需求分析、任务拆解、项目进度跟踪",
      "capabilities": ["需求分析", "项目规划", "进度追踪", "团队协调", "风险管理"],
      "specialties": ["python", "web开发", "数据库设计", "敏捷开发"]
    },
    {
      "node_id": "work-analyst-001",
      "node_name": "分析师-李四",
      "role": "work",
      "role_tag": "analyst",
      "advertise_host": "192.168.1.102",
      "advertise_port": 8899,
      "status": "online",
      "description": "我是需求分析师，负责业务逻辑梳理、用例设计、需求评审",
      "capabilities": ["业务分析", "用例设计", "需求评审", "流程优化"],
      "specialties": ["UML建模", "Axure原型设计", "业务建模"]
    },
    {
      "node_id": "work-architect-001",
      "node_name": "架构师-王五",
      "role": "work",
      "role_tag": "architect",
      "advertise_host": "192.168.1.103",
      "advertise_port": 8899,
      "status": "online",
      "description": "我是系统架构师，负责系统架构设计、技术方案输出、代码审查",
      "capabilities": ["架构设计", "技术方案", "代码审查", "性能优化"],
      "specialties": ["微服务", "分布式系统", "云原生", "K8s"]
    }
  ]
}
```

### 5.2 本地配置文件生成

```python
# cluster/nodes_cache.py

class NodesCacheManager:
    """节点列表缓存管理器"""
    
    def __init__(self, cache_path: str = "./data/cluster_nodes.json"):
        self._cache_path = cache_path
        self._nodes: dict[str, ClusterNode] = {}
        self._lock = asyncio.Lock()
    
    async def update_from_registry(self, registry: ClusterRegistry) -> None:
        """从注册中心更新节点列表"""
        async with self._lock:
            all_nodes = await registry.get_all_nodes()
            
            self._nodes = {node.node_id: node for node in all_nodes}
            
            # 生成 cluster_nodes.json
            await self._write_cache_file()
            
            logger.info(f"[NodesCache] Updated {len(all_nodes)} nodes to {self._cache_path}")
    
    async def _write_cache_file(self) -> None:
        """写入本地配置文件"""
        os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
        
        cache_data = {
            "version": "1.27.13",
            "generated_at": datetime.now().isoformat(),
            "node_id": os.getenv("NODE_ID"),
            "role": os.getenv("NODE_ROLE"),
            "nodes": [node.to_dict() for node in self._nodes.values()]
        }
        
        with open(self._cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    
    async def get_work_nodes_for_llm(self) -> list[dict]:
        """获取 Work 节点描述列表（用于 LLM 上下文）"""
        work_nodes = [
            {
                "node_id": node.node_id,
                "node_name": node.node_name,
                "role_tag": node.role_tag,
                "description": node.description,
                "capabilities": node.capabilities,
                "specialties": node.specialties,
                "status": node.status,
            }
            for node in self._nodes.values()
            if node.role == "work" and node.status == "online"
        ]
        
        return work_nodes
    
    async def find_node_by_capability(self, capability: str) -> list[ClusterNode]:
        """根据能力查找节点"""
        return [
            node for node in self._nodes.values()
            if capability in node.capabilities and node.status == "online"
        ]
```

---

## 六、Master LLM 上下文增强

### 6.1 上下文加载机制

```python
# cluster/master/context_loader.py

class MasterContextLoader:
    """Master LLM 上下文加载器"""
    
    SYSTEM_PROMPT_TEMPLATE = """你是一个多角色 AI 集群管理系统的主控节点（Master）。

当前集群中有以下 Work 节点可以使用：

{work_nodes_context}

在分配任务时，请根据任务的性质和 Work 节点的能力描述，智能选择最合适的 Work 节点：
1. 需求分析类任务 → 选择【需求分析师】节点
2. 架构设计类任务 → 选择【系统设计师】节点
3. 代码开发类任务 → 选择【开发工程师】节点
4. 测试验证类任务 → 选择【测试工程师】节点
5. 项目管理类任务 → 选择【PM】节点

当你需要同时协调多个 Work 节点时，请确保任务之间的依赖关系正确排序。
"""
    
    def __init__(
        self,
        nodes_cache: NodesCacheManager,
        max_works_in_context: int = 50
    ):
        self._nodes_cache = nodes_cache
        self._max_works_in_context = max_works_in_context
        self._cached_prompt: str | None = None
        self._cache_timestamp: datetime | None = None
        self._cache_ttl_seconds = 300  # 5 分钟刷新一次
    
    async def get_system_prompt(self) -> str:
        """获取增强后的 System Prompt"""
        now = datetime.now()
        
        # 检查缓存是否过期
        if self._cached_prompt and self._cache_timestamp:
            elapsed = (now - self._cache_timestamp).total_seconds()
            if elapsed < self._cache_ttl_seconds:
                return self._cached_prompt
        
        # 重新生成
        work_nodes = await self._nodes_cache.get_work_nodes_for_llm()
        
        if not work_nodes:
            return self.SYSTEM_PROMPT_TEMPLATE.format(
                work_nodes_context="（当前无在线 Work 节点）"
            )
        
        # 构建 Work 节点上下文
        work_nodes_context = self._build_work_nodes_context(work_nodes[:self._max_works_in_context])
        
        self._cached_prompt = self.SYSTEM_PROMPT_TEMPLATE.format(
            work_nodes_context=work_nodes_context
        )
        self._cache_timestamp = now
        
        return self._cached_prompt
    
    def _build_work_nodes_context(self, work_nodes: list[dict]) -> str:
        """构建 Work 节点描述上下文"""
        lines = []
        
        for i, node in enumerate(work_nodes, 1):
            lines.append(f"""
【{i}. {node['node_name']}】 (ID: {node['node_id']}, 角色: {node['role_tag']})
   描述: {node['description']}
   能力: {', '.join(node['capabilities'])}
   专长: {', '.join(node['specialties'])}
   状态: {node['status']}
""")
        
        return "\n".join(lines)
```

### 6.2 Master Agent 集成

```python
# cluster/master/master_agent.py

class MasterAgent:
    """Master Agent（集成上下文加载）"""
    
    def __init__(
        self,
        orchestrator,  # 现有 orchestrator
        context_loader: MasterContextLoader,
        nodes_cache: NodesCacheManager
    ):
        self._orchestrator = orchestrator
        self._context_loader = context_loader
        self._nodes_cache = nodes_cache
        self._current_task_id: str | None = None
    
    async def get_prompt_for_task(self, task: str) -> str:
        """获取任务处理时的 Prompt（包含实时 Work 节点信息）"""
        # 获取增强后的 System Prompt
        system_prompt = await self._context_loader.get_system_prompt()
        
        # 获取当前 Work 节点状态
        work_nodes = await self._nodes_cache.get_work_nodes_for_llm()
        
        # 构建用户消息
        user_message = f"""请处理以下任务：
{task}

当前在线 Work 节点数量：{len(work_nodes)}
"""
        
        return f"{system_prompt}\n\n{user_message}"
    
    async def select_best_work_for_task(self, task_description: str) -> str | None:
        """根据任务描述选择最合适的 Work 节点"""
        work_nodes = await self._nodes_cache.get_work_nodes_for_llm()
        
        if not work_nodes:
            return None
        
        # 简单的关键词匹配（实际可用 LLM 判断）
        role_mapping = {
            "需求": "analyst",
            "分析": "analyst",
            "设计": "architect",
            "架构": "architect",
            "开发": "developer",
            "代码": "developer",
            "测试": "tester",
            "验证": "tester",
            "项目": "pm",
            "管理": "pm",
        }
        
        for keyword, role_tag in role_mapping.items():
            if keyword in task_description:
                for node in work_nodes:
                    if node["role_tag"] == role_tag:
                        return node["node_id"]
        
        # 默认返回第一个在线 Work
        return work_nodes[0]["node_id"]
```

---

## 七、共享目录机制（sharefolder）

### 7.1 目录结构设计

```
${PROJECT_ROOT}/
└── sharefolder/
    ├── master-001/           ← Master 节点目录
    │   ├── deliverables/    # 交付物目录
    │   ├── reports/         # 报告输出
    │   └── temp/            # 临时文件
    ├── work-pm-001/          ← PM Work 节点目录
    │   ├── requirements/    # 需求文档
    │   ├── plans/           # 项目计划
    │   └── reports/         # 进度报告
    ├── work-analyst-001/     ← 需求分析师目录
    │   ├── specs/           # 需求规格
    │   └── use_cases/       # 用例文档
    ├── work-architect-001/   ← 系统设计师目录
    │   ├── designs/         # 设计文档
    │   └── diagrams/        # 架构图
    ├── work-developer-001/   ← 开发工程师目录
    │   ├── code/           # 代码文件
    │   ├── tests/           # 测试代码
    │   └── docs/           # 开发文档
    ├── work-tester-001/      ← 测试工程师目录
    │   ├── test_plans/     # 测试计划
    │   ├── test_cases/      # 测试用例
    │   └── bugs/           # Bug 报告
    └── .metadata/           ← 元数据目录（系统使用）
        ├── quotas.json      # 各节点配额配置
        └── audit.log        # 访问审计日志
```

### 7.2 ShareFolderManager 实现

```python
# cluster/sharefolder/manager.py

import os
import asyncio
import json
from pathlib import Path
from typing import Optional

class ShareFolderManager:
    """共享目录管理器"""
    
    def __init__(
        self,
        sharefolder_root: str = "./sharefolder/",
        node_id: str = "",
        read_others: bool = True,
        max_size_gb: int = 10
    ):
        self._root = Path(sharefolder_root).resolve()
        self._node_id = node_id
        self._read_others = read_others
        self._max_size_bytes = max_size_gb * 1024 * 1024 * 1024
        self._node_dir = self._root / node_id
        self._metadata_dir = self._root / ".metadata"
    
    async def initialize(self) -> None:
        """初始化共享目录"""
        # 创建根目录
        self._root.mkdir(parents=True, exist_ok=True)
        
        # 创建元数据目录
        self._metadata_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建节点目录
        self._node_dir.mkdir(parents=True, exist_ok=True)
        
        # 初始化配额文件
        quotas_file = self._metadata_dir / "quotas.json"
        if not quotas_file.exists():
            await self._init_quotas_file(quotas_file)
        
        logger.info(f"[ShareFolder] Initialized at {self._root}")
    
    async def _init_quotas_file(self, quotas_file: Path) -> None:
        """初始化配额文件"""
        quotas = {
            str(self._node_id): {
                "max_size_gb": 10,
                "current_size_bytes": 0,
                "created_at": datetime.now().isoformat()
            }
        }
        with open(quotas_file, "w") as f:
            json.dump(quotas, f)
    
    async def write_file(self, relative_path: str, content: bytes) -> bool:
        """在节点目录下创建/修改文件"""
        # 安全检查：必须在节点目录下
        target = (self._node_dir / relative_path).resolve()
        if not str(target).startswith(str(self._node_dir)):
            raise PermissionError(f"Access denied: {relative_path} is outside node directory")
        
        # 检查配额
        if not await self._check_quota(len(content)):
            raise QuotaExceededError(f"Node {self._node_id} quota exceeded")
        
        # 确保目录存在
        target.parent.mkdir(parents=True, exist_ok=True)
        
        # 写入文件
        await asyncio.to_thread(target.write_bytes, content)
        
        # 更新配额
        await self._update_quota(len(content))
        
        logger.info(f"[ShareFolder] Node {self._node_id} wrote {len(content)} bytes to {relative_path}")
        return True
    
    async def read_file(self, node_id: str, relative_path: str) -> bytes:
        """读取其他节点目录的文件（只读）"""
        if not self._read_others and node_id != self._node_id:
            raise PermissionError(f"Node {self._node_id} cannot read others' directories")
        
        target = (self._root / node_id / relative_path).resolve()
        
        # 安全检查：必须在对应节点目录下
        if not str(target).startswith(str(self._root / node_id)):
            raise PermissionError(f"Access denied: {relative_path} is invalid path")
        
        if not target.exists():
            raise FileNotFoundError(f"File not found: {node_id}/{relative_path}")
        
        return await asyncio.to_thread(target.read_bytes)
    
    async def delete_file(self, relative_path: str) -> bool:
        """删除节点目录下的文件"""
        target = (self._node_dir / relative_path).resolve()
        
        if not str(target).startswith(str(self._node_dir)):
            raise PermissionError(f"Access denied: {relative_path} is outside node directory")
        
        if not target.exists():
            return False
        
        size = target.stat().st_size
        target.unlink()
        
        # 更新配额
        await self._update_quota(-size)
        
        logger.info(f"[ShareFolder] Node {self._node_id} deleted {relative_path}")
        return True
    
    async def list_node_directory(self, node_id: str) -> list[str]:
        """列出节点目录内容"""
        node_dir = self._root / node_id
        
        if not node_dir.exists():
            return []
        
        return [str(p.relative_to(node_dir)) for p in node_dir.rglob("*") if p.is_file()]
    
    async def _check_quota(self, additional_bytes: int) -> bool:
        """检查配额是否足够"""
        quotas = await self._load_quotas()
        current = quotas.get(self._node_id, {}).get("current_size_bytes", 0)
        
        return (current + additional_bytes) <= self._max_size_bytes
    
    async def _update_quota(self, delta_bytes: int) -> None:
        """更新配额使用量"""
        quotas_file = self._metadata_dir / "quotas.json"
        quotas = await self._load_quotas()
        
        if self._node_id not in quotas:
            quotas[self._node_id] = {
                "max_size_gb": 10,
                "current_size_bytes": 0,
                "created_at": datetime.now().isoformat()
            }
        
        quotas[self._node_id]["current_size_bytes"] += delta_bytes
        
        with open(quotas_file, "w") as f:
            json.dump(quotas, f, indent=2)
    
    async def _load_quotas(self) -> dict:
        """加载配额数据"""
        quotas_file = self._metadata_dir / "quotas.json"
        
        if not quotas_file.exists():
            return {}
        
        with open(quotas_file) as f:
            return json.load(f)
```

### 7.3 权限控制矩阵

| 操作 | 自己的目录 (node_id/) | 其他节点目录 |
|------|---------------------|--------------|
| **创建文件** | ✅ 允许 | ❌ 禁止 |
| **修改文件** | ✅ 允许 | ❌ 禁止 |
| **删除文件** | ✅ 允许 | ❌ 禁止 |
| **读取文件** | ✅ 允许 | ✅ 允许（如果 read_others=true） |
| **列出目录** | ✅ 允许 | ✅ 允许（列出文件列表） |
| **创建子目录** | ✅ 允许 | ❌ 禁止 |

### 7.4 使用示例

```python
# Work 节点保存交付物

sharefolder = ShareFolderManager(
    sharefolder_root="./sharefolder/",
    node_id="work-developer-001",
    read_others=True
)

# 1. 在自己的目录下创建文件
await sharefolder.write_file(
    relative_path="deliverables/ecommerce-api-v1.zip",
    content=zip_bytes
)

# 2. 读取其他节点的交付物
design_doc = await sharefolder.read_file(
    node_id="work-architect-001",
    relative_path="designs/ecommerce-architecture.pdf"
)

# 3. Master 汇总所有节点成果
all_deliverables = []
for node_id in ["work-developer-001", "work-tester-001"]:
    files = await sharefolder.list_node_directory(node_id)
    for f in files:
        if "deliverable" in f:
            all_deliverables.append({
                "node_id": node_id,
                "file": f,
                "path": f"sharefolder/{node_id}/{f}"
            })
```

---

## 八、任务链与协作模式

### 8.1 任务状态枚举（统一）

**⚠️ 重要修正**：直接 import 现有枚举，不重复定义。

```python
# cluster/task_chain.py

from openakita.orgs.models import TaskStatus  # ✅ 直接复用，不重复定义
```

### 8.2 协作流程示例

```
[完整任务流程]

用户(Master) 下达任务: "开发一个电商系统"
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ Stage 1: PM Work                                            │
│   任务: 需求分析                                             │
│   执行: PM 领取任务 → 需求分析 → 输出需求文档 → 结果反馈 Master│
│   交付物: sharefolder/work-pm-001/requirements/             │
└──────────────────────────────────────────────────────────────┘
    │ (需求文档存储在共享目录，路径通过消息传递给 Master)
    ▼
┌──────────────────────────────────────────────────────────────┐
│ Stage 2: 需求分析师 Work                                     │
│   任务: 详细需求 + 用例设计                                   │
│   执行: 分析师领取任务 → 详细分析 → 输出用例 → 结果反馈 Master │
│   交付物: sharefolder/work-analyst-001/specs/                 │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ Stage 3: 系统设计师 Work                                    │
│   任务: 架构设计 + 技术方案                                   │
│   执行: 设计师领取任务 → 架构设计 → 输出设计文档 → 反馈 Master │
│   交付物: sharefolder/work-architect-001/designs/           │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ Stage 4: 开发工程师 Work(s)                                 │
│   任务: 代码实现                                             │
│   执行: 开发领取任务 → 编码 → 单元测试 → 提交 → 反馈 Master   │
│   (开发过程中可向设计师/PM 请求帮助)                          │
│   交付物: sharefolder/work-developer-001/code/               │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ Stage 5: 测试工程师 Work                                    │
│   任务: 测试验证                                             │
│   执行: 测试领取任务 → 测试计划 → 执行测试 → Bug反馈 → 汇总   │
│   交付物: sharefolder/work-tester-001/test_reports/          │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
[Master] 汇总所有阶段交付物 → Review → 输出最终结果给用户
```

### 8.3 网络分区与幂等性处理

```python
# cluster/task_chain.py

class ClusterTask:
    async def submit_result(self, result: dict) -> bool:
        """提交任务结果（幂等操作）"""
        
        # 1. 使用乐观锁防止重复提交
        updated = await self._pg.execute("""
            UPDATE cluster_tasks 
            SET status = 'completed', result = $1, updated_at = NOW()
            WHERE id = $2 AND status IN ('in_progress', 'pending')
        """, json.dumps(result), self.task_id)
        
        if updated.rowcount == 0:
            logger.info(f"[Task] Result already submitted for {self.task_id}")
            return False
        
        return True
```

---

## 九、代码结构（V3）

### 9.1 新增模块结构

```
/root/projects/openakita/src/openakita/cluster/
├── __init__.py
├── config.py                      # 配置加载 + 校验（新增描述字段校验）
├── protocol.py                    # 消息协议（扩展 MsgType，不新建枚举）
├── models.py                       # 数据模型（ClusterNode 扩展描述字段）
├── registry.py                     # 注册中心（支持 PostgreSQL 降级）
├── message_bus.py                 # 消息总线（Redis Pub/Sub + PG 备份）
├── gateway.py                     # WebSocket 网关（增加连接数限制）
├── heartbeat.py                   # 心跳管理（完整时间线）
├── task_chain.py                  # 任务链（import TaskStatus，幂等设计）
├── nodes_cache.py                 # 节点列表缓存管理器（V3 新增）
├── sharefolder/                   # 共享目录（V3 新增）
│   ├── __init__.py
│   ├── manager.py                 # ShareFolderManager
│   └── quota.py                   # 配额管理
├── failover/
│   ├── __init__.py
│   ├── election.py               # Master 选举（Redlock）
│   ├── snapshot.py               # 任务快照
│   └── recovery.py               # 故障恢复
├── master/
│   ├── __init__.py
│   ├── scheduler.py              # Master 调度器
│   ├── task_aggregator.py        # 结果汇聚器
│   ├── context_loader.py         # LLM 上下文加载器（V3 新增）
│   └── ws_server.py             # WebSocket 服务端（带认证）
├── work/
│   ├── __init__.py
│   ├── executor.py               # Work 执行器
│   ├── help_seeker.py            # 互助请求器（完整发现机制）
│   └── ws_client.py             # WebSocket 客户端（重连策略）
└── storage/
    ├── __init__.py
    ├── redis_store.py            # Redis 存储封装
    ├── postgres_store.py         # PostgreSQL 存储封装
    └── s3_store.py              # S3 存储封装
```

### 9.2 V3 新增文件清单

| 文件 | 说明 |
|------|------|
| `cluster/models.py` | 新增 ClusterNode 数据模型（包含描述字段） |
| `cluster/nodes_cache.py` | 新增节点列表缓存管理器 |
| `cluster/sharefolder/__init__.py` | 新增共享目录模块 |
| `cluster/sharefolder/manager.py` | 新增 ShareFolderManager |
| `cluster/sharefolder/quota.py` | 新增配额管理 |
| `cluster/master/context_loader.py` | 新增 Master LLM 上下文加载器 |

### 9.3 复用现有组件

| 现有组件 | 复用方式 | 说明 |
|----------|----------|------|
| `orgs.models.TaskStatus` | ✅ 直接 import | 不重复定义 |
| `orgs.models.MsgType` | ✅ 扩展而非新建 | 增加 CLUSTER_* 类型 |
| `orgs.models.OrgMessage` | ✅ 扩展字段 | 增加 scope 字段 |
| `orgs.models.Organization` | ✅ 数据模型 | 复用字段定义 |
| `orgs.runtime.OrgRuntime` | ✅ 本地运行时 | 每个节点运行独立实例 |
| `orgs.blackboard.OrgBlackboard` | ✅ 本地记忆 | 节点内部使用 |
| `orgs.scaler.OrgScaler` | ✅ 扩缩容策略 | 参考其扩缩容逻辑 |

---

## 十、.env 配置模板（V3 完整版）

### 10.1 Master 节点 .env 模板

```bash
# =============================================================
# OpenAkita Cluster - Master Node Configuration (V3)
# =============================================================

# === 节点身份 ===
NODE_ROLE=master
NODE_ID=master-001
NODE_NAME="OpenAkita Master"
NODE_DESCRIPTION="我是集群中枢大脑，负责统一调度管理、任务分发、结果汇总、记忆汇聚"
NODE_CAPABILITIES="任务调度,结果汇总,记忆汇聚,故障恢复,负载均衡"
NODE_SPECIALTIES="系统架构,性能优化,分布式系统"
CLUSTER_AUTH_TOKEN=changeme_master_token_abc123xyz
CLUSTER_ORG_ID=default_org

# === 网络配置 ===
CLUSTER_BIND_HOST=0.0.0.0
CLUSTER_BIND_PORT=8899
CLUSTER_ADVERTISE_HOST=192.168.1.100
CLUSTER_ADVERTISE_PORT=8899

# === 连接数限制 ===
MASTER_MAX_CONNECTIONS=100
MASTER_RATE_LIMIT_PER_SEC=1000
WORK_OUTBOUND_QUEUE_SIZE=100

# === 注册中心 - Redis 主 ===
REGISTRY_MODE=self-hosted
REGISTRY_BACKEND=redis
REDIS_HOST=192.168.1.100
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password_here
REDIS_DB=0
REDIS_TLS_ENABLED=false

# === 注册中心 - PostgreSQL 备选 ===
DB_FALLBACK_ENABLED=true
DB_HOST=192.168.1.100
DB_PORT=5432
DB_NAME=openakita_cluster
DB_USER=postgres
DB_PASSWORD=your_db_password_here
DB_SSL_MODE=prefer

# === 心跳配置 ===
HEARTBEAT_INTERVAL_SEC=10
HEARTBEAT_TIMEOUT_SEC=40
HEARTBEAT_MISSING_THRESHOLD=40
HEARTBEAT_OFFLINE_THRESHOLD=60
HEARTBEAT_GONE_THRESHOLD=180

# === 对象存储 ===
STORAGE_TYPE=s3
STORAGE_ENDPOINT=http://192.168.1.100:9000
STORAGE_BUCKET=openakita-cluster
STORAGE_ACCESS_KEY=your_access_key_here
STORAGE_SECRET_KEY=your_secret_key_here

# === 集群成员（种子节点）===
CLUSTER_SEED_NODES=192.168.1.101:8899,192.168.1.102:8899,192.168.1.103:8899

# === Master 特定配置 ===
MASTER_TASK_AGGREGATION=enabled
MASTER_AUTO_TASK_DISPATCH=enabled
MASTER_SNAPSHOT_INTERVAL_SEC=300
MASTER_ELECTION_LOCK_TTL_SEC=30

# === LLM 上下文配置（V3）===
MASTER_LLM_CONTEXT_LOAD_WORK_DESCRIPTIONS=true
MASTER_LLM_CONTEXT_MAX_WORKS=50
CLUSTER_NODES_CACHE_PATH=./data/cluster_nodes.json

# === 共享目录配置（V3）===
SHAREFOLDER_ENABLED=true
SHAREFOLDER_ROOT=./sharefolder/
SHAREFOLDER_NODE_DIR=${SHAREFOLDER_ROOT}/${NODE_ID}/
SHAREFOLDER_READ_OTHERS=true
SHAREFOLDER_MAX_SIZE_GB=10

# === 日志 ===
LOG_LEVEL=INFO
LOG_DIR=./logs/cluster

# === 启动配置校验 ===
CONFIG_VALIDATION_STRICT=true
```

### 10.2 Work 节点 .env 模板（按角色）

#### PM Work

```bash
# === 节点身份 ===
NODE_ROLE=work
NODE_ID=work-pm-001
NODE_NAME="PM-张三"
NODE_ROLE_TAG=pm
NODE_DESCRIPTION="我是项目经理，负责需求分析、任务拆解、项目进度跟踪"
NODE_CAPABILITIES="需求分析,项目规划,进度追踪,团队协调,风险管理"
NODE_SPECIALTIES="python,web开发,数据库设计,敏捷开发"
CLUSTER_AUTH_TOKEN=changeme_master_token_abc123xyz
CLUSTER_ORG_ID=default_org

# === 网络配置 ===
CLUSTER_BIND_HOST=0.0.0.0
CLUSTER_BIND_PORT=8899
CLUSTER_ADVERTISE_HOST=192.168.1.101
CLUSTER_ADVERTISE_PORT=8899

MASTER_HOST=192.168.1.100
MASTER_PORT=8899

# === 注册中心 ===
REDIS_HOST=192.168.1.100
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password_here
REDIS_DB=0

DB_HOST=192.168.1.100
DB_PORT=5432
DB_NAME=openakita_cluster
DB_USER=postgres
DB_PASSWORD=your_db_password_here

# === 心跳配置 ===
HEARTBEAT_INTERVAL_SEC=10
HEARTBEAT_TIMEOUT_SEC=40

# === Work 特定配置 ===
WORK_MAX_CONCURRENT_TASKS=2
WORK_CAN_SEEK_HELP=true
WORK_HELP_REQUEST_TIMEOUT_SEC=300

# === 共享目录配置（V3）===
SHAREFOLDER_ENABLED=true
SHAREFOLDER_ROOT=./sharefolder/
SHAREFOLDER_NODE_DIR=${SHAREFOLDER_ROOT}/${NODE_ID}/
SHAREFOLDER_READ_OTHERS=true
SHAREFOLDER_MAX_SIZE_GB=10

# === 日志 ===
LOG_LEVEL=INFO
LOG_DIR=./logs/cluster
```

#### 需求分析师 Work

```bash
NODE_ROLE=work
NODE_ID=work-analyst-001
NODE_NAME="分析师-李四"
NODE_ROLE_TAG=analyst
NODE_DESCRIPTION="我是需求分析师，负责业务逻辑梳理、用例设计、需求评审"
NODE_CAPABILITIES="业务分析,用例设计,需求评审,流程优化"
NODE_SPECIALTIES="UML建模,Axure原型设计,业务建模"
```

#### 系统设计师 Work

```bash
NODE_ROLE=work
NODE_ID=work-architect-001
NODE_NAME="架构师-王五"
NODE_ROLE_TAG=architect
NODE_DESCRIPTION="我是系统架构师，负责系统架构设计、技术方案输出、代码审查"
NODE_CAPABILITIES="架构设计,技术方案,代码审查,性能优化"
NODE_SPECIALTIES="微服务,分布式系统,云原生,K8s"
```

#### 开发工程师 Work

```bash
NODE_ROLE=work
NODE_ID=work-developer-001
NODE_NAME="开发-赵六"
NODE_ROLE_TAG=developer
NODE_DESCRIPTION="我是后端开发工程师，负责代码实现、单元测试、API开发"
NODE_CAPABILITIES="代码开发,单元测试,API设计,代码审查"
NODE_SPECIALTIES="Python,Java,Go,数据库,缓存系统"
```

#### 测试工程师 Work

```bash
NODE_ROLE=work
NODE_ID=work-tester-001
NODE_NAME="测试-钱七"
NODE_ROLE_TAG=tester
NODE_DESCRIPTION="我是测试工程师，负责测试计划、测试用例、缺陷跟踪"
NODE_CAPABILITIES="测试计划,测试用例设计,缺陷跟踪,回归测试"
NODE_SPECIALTIES="自动化测试,性能测试,安全测试,测试工具"
```

---

## 十一、配置加载与校验（V3）

```python
# cluster/config.py

def validate_config() -> list[str]:
    """配置校验（V3 增强版），返回错误列表"""
    errors = []
    
    # 1. 身份校验
    if not NODE_ID:
        errors.append("NODE_ID is required")
    if NODE_ROLE not in ("master", "work"):
        errors.append(f"NODE_ROLE must be 'master' or 'work', got '{NODE_ROLE}'")
    if not CLUSTER_AUTH_TOKEN or CLUSTER_AUTH_TOKEN.startswith("changeme"):
        errors.append("CLUSTER_AUTH_TOKEN must be set and not be default value")
    
    # 2. 描述信息校验（V3 新增）
    if not NODE_DESCRIPTION:
        errors.append("NODE_DESCRIPTION is required for all nodes")
    if not NODE_CAPABILITIES:
        errors.append("NODE_CAPABILITIES is required for all nodes")
    if not NODE_SPECIALTIES:
        errors.append("NODE_SPECIALTIES is required for all nodes")
    
    # 3. 网络配置校验
    if not CLUSTER_ADVERTISE_HOST:
        errors.append("CLUSTER_ADVERTISE_HOST is required for work nodes")
    if NODE_ROLE == "work" and not MASTER_HOST:
        errors.append("MASTER_HOST is required for work nodes")
    
    # 4. 存储配置校验
    if not REDIS_PASSWORD:
        errors.append("REDIS_PASSWORD is required")
    if not DB_PASSWORD:
        errors.append("DB_PASSWORD is required")
    
    # 5. Work 角色标签校验（V3 新增）
    if NODE_ROLE == "work" and not NODE_ROLE_TAG:
        errors.append("NODE_ROLE_TAG is required for work nodes")
    if NODE_ROLE == "work" and NODE_ROLE_TAG not in ("pm", "analyst", "architect", "developer", "tester"):
        errors.append(f"NODE_ROLE_TAG must be one of: pm, analyst, architect, developer, tester, got '{NODE_ROLE_TAG}'")
    
    # 6. 共享目录配置校验（V3 新增）
    if SHAREFOLDER_ENABLED:
        if not SHAREFOLDER_ROOT:
            errors.append("SHAREFOLDER_ROOT is required when SHAREFOLDER_ENABLED=true")
        if SHAREFOLDER_MAX_SIZE_GB <= 0 or SHAREFOLDER_MAX_SIZE_GB > 1000:
            errors.append("SHAREFOLDER_MAX_SIZE_GB must be between 1 and 1000")
    
    return errors
```

---

## 十二、总结

### 12.1 V3 新增功能点

| # | 功能 | 说明 |
|---|------|------|
| 1 | NODE_DESCRIPTION | 角色定位描述 |
| 2 | NODE_CAPABILITIES | 能力列表（用于智能任务委派） |
| 3 | NODE_SPECIALTIES | 技术专长列表 |
| 4 | 注册时携带描述 | ClusterNode.to_dict() 包含描述字段 |
| 5 | cluster_nodes.json | 启动时生成本地节点列表缓存 |
| 6 | Master LLM 上下文增强 | 加载 Work 描述到 System Prompt |
| 7 | ShareFolderManager | 共享目录权限控制 |
| 8 | 节点目录隔离 | sharefolder/{node_id}/ 读写权限控制 |
| 9 | 配额管理 | 各节点目录最大存储限制 |

### 12.2 关键技术点

| 技术点 | 实现 |
|--------|------|
| 节点描述注册 | ClusterNode 扩展 capabilities/specialties 字段 |
| 本地缓存 | NodesCacheManager.update_from_registry() 生成 JSON |
| LLM 上下文 | MasterContextLoader.get_system_prompt() 动态构建 |
| 共享目录 | ShareFolderManager.write_file/read_file 权限校验 |
| 配额控制 | quota.py 基于 .metadata/quotas.json 管理 |

---

**文档状态**：已完成，可进入实现阶段  
**下一步**：基于本设计文档开始实现 cluster/ 模块