# OpenAkita Mode2 关系型图谱记忆深挖

本文专门解释 OpenAkita 的 `mode2` 关系型图谱记忆。

重点不是重复“mode2 更高级”这种泛泛描述，而是回答：

1. `mode1` 和 `mode2` 到底各自存什么
2. `mode2` 的真实数据结构是什么
3. 它是如何编码、整合、检索的
4. 它相对 `mode1` 真正强在哪里
5. 它又带来了哪些成本和限制

## 1. 先给结论

`mode1` 和 `mode2` 的真实差异，不是“一个简单，一个复杂”。

更准确地说：

- `mode1` 是“结构化语义记忆库”
- `mode2` 是“多维关系事件图”

所以：

- `mode1` 擅长回答“用户有什么偏好/规则/事实/经验”
- `mode2` 擅长回答“事情是怎么发生的、前后关系是什么、哪个实体和哪个事件有关、为什么会这样”

这两个模式不是互斥替代关系，而是：

- `mode1` 负责长期事实与经验片段
- `mode2` 负责事件、时间线、因果链、实体关联

## 2. mode1 的真实形态

### 2.1 存储对象

`mode1` 的主体对象是 `SemanticMemory`。

它关注的是：

- `type`
- `priority`
- `content`
- `subject`
- `predicate`
- `importance`
- `confidence`
- `scope`
- `user_id`
- `workspace_id`
- `expires_at`

这意味着它更像：

- “用户偏好简洁风格”
- “项目默认使用 Python 3.11”
- “之前这个错误是由 compose 映射冲突导致”

这样的结构化事实条目。

### 2.2 mode1 的核心能力

mode1 擅长：

- 存偏好
- 存规则
- 存事实
- 存经验
- 做语义相似检索
- 做作用域隔离
- 做优先级与过期控制

但它天然不强的一点是：

- 对“事件之间的关系”表达力有限

它可以记住：

- A 发生过
- B 发生过

但不天然擅长表达：

- A 导致了 B
- B 属于项目 X 的第 3 个阶段
- C 与 A/B 在同一时间线里

## 3. mode2 的真实形态

### 3.1 核心不是 memory 条目，而是图

mode2 不再把长期记忆看成一堆独立事实，而是把会话编码成：

- `MemoryNode`
- `MemoryEdge`

即：

- 节点
- 边

### 3.2 节点类型

节点类型包括：

- `event`
- `fact`
- `decision`
- `goal`

一个节点不只是文本，还带：

- `occurred_at`
- `valid_from` / `valid_until`
- `entities`
- `action_verb`
- `action_category`
- `session_id`
- `project`
- `goal`
- `importance`
- `confidence`
- `user_id`
- `workspace_id`

这说明 mode2 的最小单位不是“知识句子”，而是“一个带上下文的事件/事实节点”。

### 3.3 边类型

边类型分成五大维度：

- temporal
- causal
- entity
- action
- context

具体边例如：

- `FOLLOWED_BY`
- `LED_TO`
- `BLOCKED_BY`
- `INVOLVES`
- `RELATED_TO`
- `REQUIRES`
- `PART_OF`
- `BELONGS_TO_PROJECT`
- `SERVES_GOAL`

这说明 mode2 真正增加的是“关系表达力”。

## 4. mode2 的底层表结构

mode2 的图并不是内存态对象，而是落库到 SQLite 的多张表：

- `mdrm_nodes`
- `mdrm_edges`
- `mdrm_entity_index`
- `mdrm_reachable`
- `mdrm_entity_aliases`

### 4.1 `mdrm_nodes`

存节点本体：

- 事件/事实内容
- 时间
- 实体
- 动作
- project / goal / session
- importance / confidence
- user/workspace/agent 隔离信息

### 4.2 `mdrm_edges`

存关系边：

- source
- target
- edge_type
- dimension
- weight
- metadata

### 4.3 `mdrm_entity_index`

存实体到节点的倒排索引：

- 哪个实体出现在什么节点里

### 4.4 `mdrm_reachable`

是物化的可达表：

- 预计算 1~2 hop 的可达路径
- 让检索时不用每次从原始边现算

### 4.5 `mdrm_entity_aliases`

是实体别名表：

- 用来解决实体名归一化问题

## 5. mode2 的编码流程

`mode2` 不是一次性 LLM 全包，而是三层编码。

### 5.1 第一层：快速规则编码

`encode_quick()`

特点：

- 无 LLM
- 低延迟
- 从 turn 和 tool_calls 中先提基本节点
- 补 temporal chain
- 补 entity 共现边

适合：

- 压缩前的快速预编码
- 没有 brain 时的降级

这层的意义是先建立“基础骨架”。

### 5.2 第二层：从压缩摘要回填

`backfill_from_summary()`

特点：

- 读取摘要
- 新建 summary node
- 给 partial nodes 补 context 边
- 如果摘要中有因果语义，再补 causal 边

这层的意义是：

- 用“压缩后对整段对话的理解”反哺图结构

### 5.3 第三层：session 结束时的批量 LLM 编码

`encode_session()`

特点：

- 取整段会话文本
- 交给 LLM 输出合法 JSON
- 解析出更完整的 nodes / edges
- 再把 quick-encoded nodes 和 LLM nodes 通过 context 边连起来

这层的意义是：

- 用更全局的视角补全事件图

## 6. mode2 的检索方式

mode1 的检索核心是：

- 语义搜索
- FTS5 / 向量相似
- Episode / attachments / recent 混合召回

mode2 的检索核心是：

- 先找 seed nodes
- 再按维度遍历图
- 最后综合打分

### 6.1 查询理解

`GraphEngine` 会先把用户查询解析成“维度线索”：

- 是否含时间线语义
- 是否含因果语义
- 是否偏实体历史
- 提取 keywords

如果用户问：

- “之前”
- “为什么”
- “历史”
- “过程”

图检索就更容易被激活。

### 6.2 seed 节点查找

会通过：

- FTS
- entity index
- LIKE
- time range

找到一批初始节点。

### 6.3 图遍历

然后会按维度从 seed 沿边扩散：

- temporal
- causal
- entity
- context

并考虑：

- hop 数
- edge 权重
- 多维匹配程度

### 6.4 结果裁剪

最后再按：

- relevance
- matched dimensions
- token budget

裁成可注入 prompt 的结果。

所以 mode2 的回答基础不是“相似文本片段”，而是“相关节点子图”。

## 7. mode2 的整理与维护

图不是建完就不管了。

`RelationalConsolidator` 会做维护：

- 重建 reachable table
- 对边做时间衰减
- 剪枝弱边
- 可选实体消歧

它的思路更像“图结构维护”，而不是普通 memory 的条目管理。

### 7.1 reachable rebuild

把 1-hop 和 2-hop 可达路径物化出来。

好处：

- 检索快

代价：

- 需要后台重建

### 7.2 edge decay

所有边会按因子衰减。

这表示：

- 图里的关系强度不是永恒不变的

### 7.3 prune weak edges

权重太低的边会删掉。

这说明 mode2 的遗忘机制主要体现在：

- “关系减弱和边剪枝”

而不只是 memory 条目过期。

## 8. mode1 与 mode2 的真实差异

下面是最关键的一部分。

## 8.1 数据模型差异

### mode1

像这样：

```text
用户 -> 偏好 -> 简洁回答
项目X -> 技术栈 -> Python 3.11
错误Y -> 原因 -> compose 端口映射冲突
```

重点是：

- 结构化条目
- 主谓属性
- 适合长期事实

### mode2

像这样：

```text
节点1: 用户发起“排查 Docker 端口冲突”
节点2: 调用了 read_file / search / run_shell
节点3: 发现 compose 文件端口重复
节点4: 修改 compose 配置
节点5: 问题消失

边:
1 FOLLOWED_BY 2
2 LED_TO 3
3 ENABLES 4
4 LED_TO 5
3 INVOLVES docker-compose.yml
1 BELONGS_TO_PROJECT openakita
```

重点是：

- 事件链
- 因果链
- 实体关联
- 项目与目标上下文

## 8.2 检索语义差异

### mode1 更擅长

- “用户喜欢什么？”
- “之前有哪些经验教训？”
- “这个规则是什么？”
- “项目的固定事实是什么？”

### mode2 更擅长

- “上次这个问题是怎么一步步解决的？”
- “为什么会失败？”
- “这个实体之前还出现在哪些场景？”
- “某个项目里这类问题的历史链路是什么？”

## 8.3 时间维度差异

mode1 虽然也有 `created_at` / `updated_at`，但时间更多是排序辅助。

mode2 的时间是结构的一部分：

- `occurred_at`
- `valid_from`
- `valid_until`
- temporal edges
- time-range seed search

所以它更接近“事件时间线”。

## 8.4 因果能力差异

mode1 可以存：

- “X 是因为 Y”

但这仍然是单条事实。

mode2 可以把“因为 Y 导致 X，X 又导致 Z”编码成可遍历链条。

这让它在：

- 根因分析
- 过程解释
- 历史回溯

上更有潜力。

## 8.5 对任务轨迹的表达差异

mode1 更适合“结论”。

mode2 更适合“过程”。

这是两者最大的真实差异。

## 9. mode2 真正的收益

### 9.1 对复杂长期交互更友好

当用户反复围绕：

- 一个项目
- 一类故障
- 一个长期目标

展开多轮会话时，mode2 比 mode1 更容易保留“脉络”。

### 9.2 更适合解释型问题

用户问：

- 为什么
- 之前
- 过程
- 历史

mode2 的图检索更容易给出结构化答案。

### 9.3 对跨会话实体追踪更自然

同一实体在多个 session 里反复出现时，图模型比离散 semantic memories 更容易组织它们。

## 10. mode2 的真实代价

### 10.1 编码更重

mode1 的主体是：

- 抽取语义记忆

mode2 还要：

- 编码节点
- 编码边
- 构造实体索引
- 维护 reachable 表

### 10.2 维护成本更高

图结构不是只增不减：

- 要衰减
- 要剪枝
- 要做实体消歧

### 10.3 查询不一定总比 mode1 准

如果用户只是问：

- “我偏好什么”
- “你记住了哪些规则”

mode2 不会天然比 mode1 更好。

因为这些问题本来就更适合语义条目。

### 10.4 对 LLM 编码质量有依赖

第三层 session 编码依赖 LLM 输出 JSON。

如果：

- LLM 不稳定
- 对话太长
- 结构理解偏差

mode2 图质量也会受影响。

## 11. 为什么默认是 `auto`

配置默认不是强制 `mode2`，而是 `auto`，这是合理的。

因为现实里：

- 不是每个问题都值得走图
- 也不是每个场景都适合只走语义检索

`auto` 的真正含义是：

- 让系统根据查询特征在“语义条目库”和“关系图检索”之间选择更合适的路径

## 12. 一个更真实的判断

mode2 不是“mode1 的升级版”，更像是“在 mode1 之上叠加的事件关系层”。

所以它的真实定位应该是：

- mode1：记住“是什么”
- mode2：记住“怎么发生的”

而不是：

- mode1 低级
- mode2 高级

## 13. 什么场景更适合 mode1

- 偏好记忆
- 用户规则
- 长期设定
- 稳定事实
- 高频低成本检索
- 对延迟更敏感的场景

## 14. 什么场景更适合 mode2

- 复杂项目跟踪
- 故障排查历史
- 根因分析
- 时间线回顾
- 多实体、多动作、多阶段任务
- 跨 session 的事件关联

## 15. 最后的结论

如果用一句话总结：

> `mode1` 像长期知识卡片库，`mode2` 像长期事件关系图谱。

再具体一点：

- `mode1` 解决“记住什么”
- `mode2` 解决“这些记忆彼此怎么关联”

OpenAkita 做 `mode2` 的价值，不是让记忆“更多”，而是让记忆从“条目集合”变成“可沿关系遍历的结构”。

这也是它区别于普通“聊天历史 + 向量检索”记忆方案的地方。

## 16. 阅读顺序建议

如果你想继续沿 mode2 深挖源码，建议顺序：

1. `src/openakita/memory/relational/types.py`
2. `src/openakita/memory/relational/store.py`
3. `src/openakita/memory/relational/encoder.py`
4. `src/openakita/memory/relational/graph_engine.py`
5. `src/openakita/memory/relational/consolidator.py`
6. `src/openakita/memory/manager.py`
7. `src/openakita/prompt/builder.py`
