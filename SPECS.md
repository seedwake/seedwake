# Seedwake（心相续）— 系统规格文档

## 1. 项目概述

### 1.1 目标

本项目模拟佛教"心相续"（santāna）概念——意识作为刹那生灭、前后相续的念头之流。系统通过循环调用本地 LLM，不间断地产生念头，形成持续的意识流。每一轮循环同时产生多个想法（默认三个），模拟意识的多线程运作。

系统具备记忆、联想、行动、感知外部世界、自我反思、睡眠整理等能力，力求还原意识运作的核心机制。

### 1.2 核心理念

- **刹那生灭**：前念灭，后念生，中间无等待。循环由上一轮完成自然驱动下一轮，不依赖定时器或心跳。
- **心所相应**：每一念不只是思维内容，还伴随注意（作意）、情绪（受）、意图（思）等心理因素。
- **阿赖耶识**：习气（种子）独立于记忆系统，记录被反复熏习的行为模式和倾向性。
- **根境相触**：系统通过外部刺激通道感知世界变化，对话、新闻、时间、系统状态等都是"尘境"。

### 1.3 思维语言

系统默认以中文为主要思维语言。Prompt 模板、念头生成、记忆存储均以中文为主，允许自然混合英文（特别是技术概念）。这直接影响 embedding 模型选型（优先选择对中文语义理解更好的模型）。

---

## 2. 系统架构

### 2.1 组件划分

当前已实现三个主要组件：`core`、`backend`、`bot`。基础设施（PostgreSQL、Redis）和外围服务（backend、bot）通过 `docker-compose.yml` 编排；核心引擎直接运行在宿主机上，以便自由访问 Ollama、行动执行器（包括 OpenClaw）和本地文件系统。`frontend` 属于第五阶段，届时将使用 Nuxt 单独实现。

```
seedwake/
├── docker-compose.yml
├── .gitignore                  # data/ 在 gitignore 中
├── SPECS.md
├── BACKGROUND.md
├── config.yml                  # 全局配置文件
├── core/                       # 心相续核心引擎（宿主机直接运行）
│   ├── requirements.txt
│   ├── main.py                 # 主循环入口
│   ├── cycle.py                # 单轮循环逻辑
│   ├── prompt_builder.py       # Prompt 组装器
│   ├── thought_parser.py       # 念头解析器
│   ├── memory/
│   │   ├── short_term.py       # Redis 短期记忆管理
│   │   ├── long_term.py        # PostgreSQL 长期记忆管理
│   │   ├── habit.py            # 习气（阿赖耶识）管理
│   │   └── identity.py         # 身份文档管理
│   ├── attention.py            # 注意力与选择机制（作意）
│   ├── emotion.py              # 情绪基调层（受蕴）
│   ├── prefrontal.py           # 前额叶功能（执行控制、抑制、规划）
│   ├── metacognition.py        # 元认知反思
│   ├── sleep.py                # 睡眠机制（浅睡、深睡）
│   ├── action.py               # 统一行动层（工具决策与执行调度）
│   ├── stimulus.py             # 外部刺激队列
│   ├── audit.py                # 审计日志
│   └── embedding.py            # Embedding 服务封装
├── backend/                    # 交互后端（API 服务）
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                 # FastAPI 入口
│   ├── routes/
│   │   ├── conversation.py     # 对话接口
│   │   ├── stream.py           # SSE 意识流推送
│   │   └── query.py            # 历史查询接口
│   └── auth.py                 # 管理员鉴权
├── bot/                        # Telegram 对话桥接
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py                 # python-telegram-bot 入口
└── data/                       # 所有数据资产（gitignore）
    ├── postgresql/
    ├── redis/
    └── logs/
```

### 2.2 Docker Compose 服务编排

```yaml
services:
  # 基础设施（暴露端口给宿主机上的 core 进程）
  postgresql:
    image: pgvector/pgvector:pg17
    ports:
      - "5432:5432"
    volumes:
      - ./data/postgresql:/var/lib/postgresql/data
    environment:
      POSTGRES_DB: seedwake
      POSTGRES_USER: seedwake
      POSTGRES_PASSWORD: ${DB_PASSWORD}

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - ./data/redis:/data

  # Web 服务
  backend:
    build: ./backend
    depends_on: [postgresql, redis]
    ports:
      - "8000:8000"
    volumes:
      - ./config.yml:/app/config.yml:ro

  bot:
    build: ./bot
    depends_on: [redis]
    volumes:
      - ./config.yml:/app/config.yml:ro

```

**架构说明**：core 引擎直接运行在宿主机上（`cd core && python main.py`），不在 Docker 容器中。原因：

- core 需要直接调用宿主机上的 Ollama（本地 GPU 推理）
- core 需要直接调用宿主机上的行动执行器（包括 OpenClaw，可能涉及系统命令、文件操作）
- 容器化 core 会增加不必要的网络复杂度，且无法方便地访问宿主机资源

core 通过 `localhost:5432` 连接 PostgreSQL，通过 `localhost:6379` 连接 Redis，通过 `localhost:11434` 连接 Ollama。64GB 显卡足以同时加载生成模型和 embedding 模型。

### 2.3 数据资产

所有运行时数据绑定到项目根目录下的 `data/` 文件夹，该目录在 `.gitignore` 中。包括：

- `data/postgresql/` — PostgreSQL 数据文件
- `data/redis/` — Redis 持久化文件
- `data/logs/` — 应用日志

## 当前工程假设（Current Assumptions）

- 当前尚无正式部署环境，PostgreSQL / Redis 数据默认视为可重建的开发数据
- 在出现不可随意重建的真实环境前，不引入正式 migration 体系；schema 变更直接更新 `schema.sql`
- 若未来进入真实部署阶段，再引入独立 migration 工具，并停止依赖手动重建数据库
- migration 工具倾向选择轻量、SQL-first 的方案（如 `dbmate`），避免在当前无 ORM 的架构里引入过重依赖

---

## 3. 核心循环（心相续引擎）

### 3.1 循环流程

每一轮循环的执行步骤：

```
1. 检查 StimulusQueue，取出待处理的外部刺激（最多1-2个，按优先级）
2. 检查异步行动队列，拉取已完成的行动结果
3. 从 Redis 取最近 N 轮念头作为短期记忆上下文
4. 对上一轮念头做 embedding，向量检索 PostgreSQL 中语义相关的长期记忆
5. 组装 Prompt（见 3.3）
6. 调用 Ollama 生成三个念头
7. 解析念头（类型、触发源、是否要求行动）
8. 注意力评估：对三个念头做权重排序，决定哪个被"注意到"
9. 更新情绪基调
10. 写入 Redis 短期记忆
11. 异步写入审计日志
12. 若有行动请求，经前额叶抑制检查后提交给统一行动层；行动层再通过独立的 `chat + tools` 调用确认工具和参数，并分发给 native tools 或 OpenClaw
13. 将新念头通过 Redis Pub/Sub 发布（供 backend SSE、Telegram 桥接等订阅）
14. 立即开始下一轮
```

步骤 11、12、13 应异步执行，不阻塞下一轮循环的开始。循环的节奏完全由 Ollama 推理速度决定。

### 3.2 念头结构

采用半结构化输出格式。Prompt 中通过 few-shot 示例引导模型使用标签前缀，保留自然语言表达自由度：

```
[思考] 也许应该先了解用户最近的需求变化，这样才能更好地调整策略。
[意图] 我想搜索最近的用户反馈数据。 {action:search, query:"用户反馈 近一周"}
[反应] 上次搜索结果显示系统响应变慢了，这让我有些担忧。 (← C140-2)
```

每个念头包含：

| 字段               | 说明                 | 来源       |
|------------------|--------------------|----------|
| cycle_id         | 循环编号，如 C142        | 系统生成     |
| index            | 本轮内序号（1/2/3）       | 系统生成     |
| thought_id       | 唯一标识，如 C142-1      | 系统生成     |
| type             | 思考 / 意图 / 反应 / 反思  | 模型输出标签   |
| content          | 自然语言内容             | 模型输出     |
| trigger_ref      | 触发源引用（前序念头ID或刺激ID） | 模型输出（可选） |
| action_request   | 行动请求（如有）           | 从内容中解析   |
| attention_weight | 注意力权重              | 注意力模块计算  |
| timestamp        | 时间戳                | 系统生成     |

模型只需在输出中自然地使用类型标签前缀和触发源引用，其余字段由程序后处理解析填充。`action_request` 在这一阶段只是行动候选；正式的工具选择与参数结构化由后续独立的 `chat + tools` 调用完成。

### 3.3 Prompt 组装

每轮 Prompt 由以下部分按固定顺序组装。大部分 Phase 4 状态段落仅在异常时渲染（条件见括号），正常周期 prompt 只包含固定段落和有内容的段落，避免噪声：

```
[1]  系统指令 + 身份文档 + 输出格式 + 示例 + 规则  （固定，合为一个 system 段）
[2]  此刻需要留意              （前额叶：仅当有 guidance 或 inhibition_notes）
[3]  此刻的自我感              （末那识：仅当有 session_context/warning/identity_notice）
[4]  此刻的情绪                （仅当某维度强度 ≥ 0.65）
[5]  最近的反思                （元认知反思，如有，去重）
[6]  我的笔记                  （持久草稿纸，如有内容）
[7]  好像有一阵子没有……        （感知提醒，如有）
[8]  最近的念头                （从 Redis，最近 N 轮 × 3 条）
[9]  浮上来的记忆              （念头触发的向量检索 top-k）
[10] 行动有了回音              （当前 + 近期 echo 缓存）
[11] 我已发起、在等执行的事    （pending actions）
[12] 我已经发起、正在等回音的事（running actions）
[13] 此刻我注意到              （被动感知：时间、系统状态等）
[14] 我对他们的印象            （impression，对话中出现的所有人）
[15] 最近的对话                （对话历史 + LLM 滚动摘要）
[16] 有人对我说话了            （当前轮新消息）
[17] 停滞警告                  （如检测到退化）
[18] 接下来的念头 --- 第 N 轮 ---（生成点）
```

总预算随 prompt 内容动态变化，典型值约 8000-15000 字符。以 262K 上下文窗口（num_ctx 建议 131072），有足够余量。

### 3.4 Prompt 模板版本管理

Prompt 模板是本项目最核心的资产，需要版本管理：

- 每个 prompt 模板有版本号（如 `v1.0`, `v1.1`）
- 审计表中每条记录包含使用的模板版本
- 模板修改时版本号递增，便于回溯分析"模板变更是否改善了念头质量"

---

## 4. 记忆系统

### 4.1 短期记忆（Redis）

Redis 中的短期记忆是一个三层结构：

**第一层：上下文窗口**（最近 N 轮，N 可配置，默认 30-50 轮）
- 每轮循环时取出，作为 Prompt 的一部分直接送入模型
- 模型可见、可联想

**第二层：缓冲区**（上下文窗口之外、尚未过期的条目）
- 模型不可见
- 用途：浅睡阶段的归档素材、重复检测的计算窗口、前端历史展示、行动状态追踪
- 过期策略：按时间或条目数上限（可配置，默认保留最近 500 轮）

**第三层：过期清除**
- 超出缓冲区的条目被删除
- 删除前由浅睡流程决定是否归档到长期记忆
- 审计表中永远保留完整记录

数据结构：Redis Sorted Set，score 为时间戳。每个条目的 value 是 JSON 序列化的念头数据。

额外使用：
- Redis Pub/Sub：核心进程发布新念头和事件，backend 订阅后通过 SSE 推送前端，bot 订阅后向管理员推送行动状态与确认请求
- Redis List/Stream：StimulusQueue，外部刺激的缓冲队列（对话类刺激由 Telegram Bot 写入）

### 4.2 长期记忆（PostgreSQL + pgvector）

长期记忆存储在 PostgreSQL 中，启用 pgvector 扩展以支持向量检索。
`long_term_memory` 表通过 `memory_type` 区分不同类型的记忆：`episodic`（原始经历，浅睡时从 STM 筛选归档）、`semantic`（浅睡时从经历中提炼的抽象认知）、`impression`（对人/事物的印象摘要）、`action_result`（行动结果）。检索时按 `相似度 × importance × time_decay_factor` 加权排序。Embedding 服务不可用时降级为按时间倒序取最近记忆。

表结构：

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE long_term_memory (
    id              BIGSERIAL PRIMARY KEY,
    content         TEXT NOT NULL,              -- 记忆内容（自然语言）
    memory_type     TEXT NOT NULL,              -- episodic（情节）/ semantic（语义/事实）/ impression（人物/事物印象）/ action_result（行动结果）
    embedding       vector(4096),               -- 向量表示（须匹配 embedding 模型输出维度）
    entity_tags     TEXT[] DEFAULT '{}',         -- 实体标签，如 {'person:alice', 'project:seedwake'}
    source_cycle_id INTEGER,                    -- 来源循环编号
    emotion_context JSONB,                      -- 当时的情绪基调快照
    importance      FLOAT DEFAULT 0.5,          -- 重要性权重（0-1）
    access_count    INTEGER DEFAULT 0,          -- 被检索次数
    last_accessed   TIMESTAMPTZ,                -- 最近被检索时间
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    is_active       BOOLEAN DEFAULT TRUE        -- 是否仍有效（遗忘/合并后标记为 FALSE）
);

-- 向量索引暂不建立：embedding 模型输出 4096 维，超出 pgvector 索引 2000 维限制
-- 当前数据规模下全表扫描可接受，后续可通过 Ollama dimensions 参数截断或 PCA 降维解决
CREATE INDEX idx_ltm_entity_tags ON long_term_memory USING GIN (entity_tags);
CREATE INDEX idx_ltm_type ON long_term_memory (memory_type);
CREATE INDEX idx_ltm_created ON long_term_memory (created_at);
```

检索策略（每轮循环步骤 4）：

1. 对上一轮最受关注的念头做 embedding
2. 向量余弦相似度检索 Top-K 条长期记忆（K 可配置，默认 5）
3. 结果按 `相似度 × importance × 时间衰减因子` 加权排序
4. 如果当前有特定实体上下文（如正在和 Alice 对话），额外按 entity_tags 过滤检索

#### 4.2.1 实体与印象

对"人"和重要"事物"维护印象摘要，存储在长期记忆的 impression 类型中，并带特殊实体标签：

```
memory_type: 'impression'
entity_tags: {'person:alice', '_impression'}
content: "关系: 管理员。印象: 技术能力强，提问直接，偏好简洁回答。最近互动: 讨论了数据库选型。情感基调: 正面。"
```

印象摘要在浅睡阶段或对话结束后更新。对话时自动检索对应人物的印象摘要注入上下文。

不需要独立的图数据库。实体关系通过 entity_tags 和 PostgreSQL 的 GIN 索引实现轻量图查询。如果后期实体关系变得非常复杂，再考虑引入图层。

### 4.3 习气（阿赖耶识）— 独立存储

习气独立于记忆系统，对应阿赖耶识中的"种子"。记忆是"我记得发生过X"，习气是"我倾向于做Y"。

存储在 PostgreSQL 的独立 schema 或表中：

```sql
CREATE TABLE habit_seeds (
    id              BIGSERIAL PRIMARY KEY,
    pattern         TEXT NOT NULL,              -- 模式描述（自然语言），如"遇到技术问题时倾向于先搜索文档"
    category        TEXT,                       -- 分类：cognitive（认知倾向）/ behavioral（行为模式）/ emotional（情绪反应模式）
    strength        FLOAT DEFAULT 0.1,          -- 强度（0-1），被强化次数越多越高
    embedding       vector(4096),               -- 向量表示，用于种子现行（§5.6）时的语义匹配
    signal_type     TEXT NOT NULL DEFAULT '',   -- 结构化控制信号类型，如 action_impulse
    signal_payload  JSONB NOT NULL DEFAULT '{}',-- 结构化控制信号负载
    activation_count INTEGER DEFAULT 0,         -- 被激活次数
    last_activated  TIMESTAMPTZ,
    source_memories BIGINT[] DEFAULT '{}',      -- 由哪些长期记忆熏习而成
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

习气的生命周期：
- **形成**：仅在浅睡阶段，由整理流程从近期经历中识别重复行为模式，创建新习气或强化已有习气
- **激活**：每轮循环时，使用当前情境的 embedding 检索最相关的几条习气；达到现行阈值的种子标记为"现行"，并转为结构化控制信号参与注意力、前额叶和元认知链路
- **衰减**：长期未被激活的习气强度逐渐降低（浅睡阶段执行）

习气不会因"遗忘"而删除，只会强度趋近于零。这符合唯识学中种子"无始以来"的特性。

### 4.4 身份文档

持久的身份文档，始终注入每轮 Prompt 的最前面。存储在 PostgreSQL：

```sql
CREATE TABLE identity (
    id              SERIAL PRIMARY KEY,
    section         TEXT NOT NULL UNIQUE,        -- 如 'self_description', 'core_goals', 'self_understanding'
    content         TEXT NOT NULL,
    version         INTEGER DEFAULT 1,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

初始内容由 bootstrap 配置提供（见第 10 节）。系统可在元认知反思阶段提议修改身份文档，但修改需经过前额叶的审慎评估。

---

## 5. 注意力与心理机制

### 5.1 注意力与选择（作意）

每轮产生三个念头后，注意力模块对其做权重排序：

- 与当前目标的相关度
- 与当前情绪基调的共鸣度
- 新颖性（与近期念头的差异度）
- 外部刺激引发的念头优先级更高

实现方式：使用结构化信号评分（bigram 文本相似度做新颖性和目标相关度、情绪维度数值做共振度、刺激类型和念头类型做优先级加分），不使用关键词硬编码匹配。

权重最高的念头成为下一轮向量检索的锚点（用它的 embedding 去长期记忆中检索），也是前端高亮展示的对象。

### 5.2 情绪基调（受蕴）

维护一个情绪状态向量，如：

```json
{
  "curiosity": 0.6,
  "calm": 0.4,
  "frustration": 0.1,
  "satisfaction": 0.3
}
```

每轮更新规则：
- 使用辅助模型做语义情绪推断（输入为当前念头和刺激的清洗文本，输出为各维度 0-1 分值），辅以结构化信号（刺激类型、行动状态等）
- 语义推断与结构化信号按自适应权重融合：LLM 输出质量高时语义权重大，输出残缺或全零时退回结构化信号主导
- 情绪有惯性：新值 = inertia × 旧值 + (1-inertia) × 本轮推断值（inertia 可配置，默认 0.7）
- 行动成功提升 satisfaction，失败提升 frustration
- 外部刺激（如用户对话）可能显著改变情绪

情绪基调作为 Prompt 的一部分注入，影响念头生成的"色彩"。

### 5.3 前额叶功能（执行控制）

独立的执行控制模块，不是每轮都运行，而是在特定条件下被激活：

**目标维持与切换**：
- 维护"当前目标栈"（存储在 Redis 中，持久化到 PostgreSQL）
- 每 N 轮（可配置）评估"最近的念头是否与目标相关"
- 持续偏离时发出修正信号

**冲动抑制**：
- 当念头包含行动请求时，经过抑制检查
- 检查逻辑：行动是否与当前目标一致？是否在白名单中？是否有风险？
- 被抑制的意图仍记录到审计日志（"我想做X但决定不做"）

**规划模式**：
- 接收到复杂任务时，连续几轮的 Prompt 切换为"规划模式"
- 专注于分解任务、排列步骤、预想障碍

实现可以用同一个 Ollama 模型但不同的 system prompt 调用，也可以用更小的模型（如 Qwen3.5-9B）做轻量判断。

### 5.4 元认知（自我观察）

每隔若干轮（可配置，默认每 50 轮），触发一次元认知反思：

- 回顾最近的念头流
- 产生关于自身状态的判断："我似乎一直在重复同一个想法"、"我的注意力被分散了"、"当前任务进展顺利"
- 可能提议修改身份文档、调整目标栈、或建议参数调整

元认知反思本身也产出念头，写入短期记忆和审计日志，类型标记为"反思"。

### 5.5 末那识（自我连续性）

对应唯识学中的第七识——恒常微细的"我执"。不是 identity.py 中的静态自我描述，而是动态的、每轮运行的自我连续性追踪。

**状态存储**：Redis key `seedwake:manas_state`，JSON 结构：

```json
{
  "self_coherence_score": 0.95,
  "last_stable_cycle": 842,
  "consecutive_disruptions": 0,
  "session_start_cycle": 800,
  "session_context": "从浅睡中醒来，精力恢复到 70",
  "identity_hash": "sha256:..."
}
```

**每轮行为**：
1. 对当前 3 个念头做**自我连续性评分**，信号至少包括：
   - 与身份文档（`self_description` / `self_understanding`）的 embedding 相似度
   - 与近期"稳定自我表述"窗口的 embedding 相似度
   - 语篇结构信号：是否从"我在经历/我想做"滑向"Seedwake 作为外部对象被描述"的脱嵌表述
   - session 连续性证据：当前轮是否承接了刚恢复的 STM / LTM / 睡眠过渡语境
2. 将这些信号融合成 `self_coherence_score`，并记录最近一次稳定连续的 cycle
3. 如果 `consecutive_disruptions >= 3`，向 prompt 注入修正提示："我注意到自己的表述在滑向旁观者视角，需要重新回到作为当下经历者的连续位置"
4. 如果 `consecutive_disruptions >= 5`，触发元认知反思

**触发条件**：
- 重启/浅睡恢复后：用 `session_context` 描述过渡（"我从浅睡中醒来"或"系统重启，我的短期记忆从 Redis 恢复"），注入 prompt 的身份段落之后
- 身份文档被修改后：比较 `identity_hash`，在下一轮 prompt 中注入"我的自我理解刚刚发生了变化"
- 如果检测到 STM 丢失、LTM 恢复不完整、或最近稳定窗口与当前念头出现明显语义断裂，也要降低 `self_coherence_score`

**不使用额外 LLM 调用**——自我连续性是底层机制，不应该增加推理延迟。实现依赖 embedding、状态计数器和语篇结构特征，不依赖固定关键词表或纯正则硬判。

### 5.6 种子现行（习气激活）

对应唯识学中"种子遇缘现行"的机制。习气不应该只是被动展示在 prompt 里等模型自己判断，而是在条件具足时主动影响念头生成。

条件匹配：使用 embedding 相似度而非关键词匹配。每轮将当前情境（最近被注意到的念头 + 当前刺激 + 当前情绪状态 + 当前目标）的 embedding 与习气库中种子的 embedding 做相似度比较，超过阈值的种子被标记为"现行"。

现行习气的影响方式分三层：
- **注意力层**：作为轻度偏置参与注意力排序，提高与当前现行习气共振的念头权重
- **前额叶层**：将结构化控制信号传给执行控制，使用 score-based gating 综合考虑动作重复度、现行习气强度、社会紧迫性、动作成本、可延期性、近期执行轨迹、以及动作是否明显在支撑当前回复；`reading / news / weather / web_fetch / search` 这类信息获取动作只获得轻度延期分，不会因前景对话被一刀切压掉，通常需要和“近期连续冒头”“现行习气”等因素叠加才会过阈值；`send_message` 不走习气抑制，只会因精确重复或对同一目标在最近 9 条且 1 小时内重复发送高相似内容而被抑制，字数过少的短确认消息（如“好的”“收到”）不进入近重复抑制，同时当它明显在回应当前前景对话时应获得正向放行分；不读取 thought 正文做关键词判断
- **元认知层**：习气原始 pattern 可作为反思上下文，帮助生成"我是否在重复旧模式"之类的反思

主 thoughts prompt 不直接展示原始习气 pattern，也不单独渲染"相关习气/倾向性"段，避免模型看到"我经常会冒出 X 冲动"后自我实现预言。主 prompt 中最多只保留抽象提醒，如"此刻有旧的惯性正在浮现，留意是否在重复旧模式"。

这需要：
1. 给 habit_seeds 表增加 embedding 列
2. 每轮做一次习气向量检索（可与 LTM 检索复用 embedding）
3. 对可结构化表达的行为习气，补充 `signal_type / signal_payload`，不要依赖自然语言 pattern 反推控制逻辑
4. 现行的种子不直接注入主 thoughts prompt，而应通过注意力排序、前额叶冲动抑制和元认知反思提供轻度加权；这种影响是偏置，不是硬覆盖

---

## 6. 外部刺激

### 6.1 StimulusQueue

统一的外部刺激入口，各种来源往其中推送，核心循环每轮开始时按优先级取出处理。

实现：Redis Stream 或 List。

每条刺激的结构：

```json
{
  "id": "stim_20260311_001",
  "type": "conversation | action_result | time | system_status | news | weather | reading | custom",
  "priority": 1,
  "source": "user:alice",
  "content": "你好，最近在忙什么？",
  "timestamp": "2026-03-11T14:30:00Z"
}
```

优先级规则（1 为最高）：
1. 用户对话消息
2. 行动结果返回
3. 系统告警（资源紧张等）
4. 低层被动感知与主动感知结果（时间感知、常规系统状态、新闻/天气/阅读结果等）

每轮最多处理 1-2 个刺激。如果队列中有用户对话，优先处理。

其中刺激来源分两类：
- **低层被动感知**：`time`、`system_status`，由系统以低频或告警方式注入
- **主动感知结果**：`news`、`weather`、`reading`，由 Seedwake 自主发起行动后回流

### 6.2 刺激类型

| 类型            | 来源                   | 频率      | 说明                                                                  |
|---------------|----------------------|---------|---------------------------------------------------------------------|
| conversation  | 用户通过 Telegram 发送     | 不定      | 最高优先级，相当于"有人对我说话"                                                   |
| action_result | 行动执行层完成回调            | 不定      | 非感知类行动的通用结果                                                         |
| time          | 低层被动感知               | 低频      | 当前时间、日期、运行时长                                                        |
| system_status | 低层被动感知 / 系统监控        | 低频 / 告警 | CPU/内存/磁盘 使用率，模拟"身体感觉"                                              |
| news          | Seedwake 主动发起新闻感知后返回 | 机会性     | 从配置中的固定 RSS feed 列表获取外界变化；已读条目按 `guid/link` 去重，使用 TTL 和上限裁剪避免状态永久膨胀 |
| weather       | Seedwake 主动发起天气感知后返回 | 机会性     | 与物理世界的锚点                                                            |
| reading       | Seedwake 主动发起阅读后返回   | 机会性     | 外部材料片段，模拟主动阅读；阅读方向由 Seedwake 自己决定，可附带 query                         |

---

## 7. 对话接口

### 7.1 Telegram 主通道

人与 Seedwake 的主对话入口是 Telegram Bot。Bot token 放在 `.env`，允许对话的 Telegram 用户 ID 列表放在 `config.yml`：

```env
TELEGRAM_BOT_TOKEN=<telegram-bot-token>
BACKEND_API_TOKEN=<backend-api-token>
```

```yaml
telegram:
  allowed_user_ids: [123456789, 987654321]
  admin_user_ids: [123456789]
```

被允许的用户向 Bot 发送私聊消息后，消息会被写入 StimulusQueue，标记为最高优先级。系统发出的 Telegram 消息仍优先通过 Telegram 返回。行动确认请求与行动状态更新只会发给 `telegram.admin_user_ids` 中的管理用户。收到消息并不意味着必须立即回复；是否发消息由 `send_message` action 决定。

### 7.2 Backend 辅助接口

backend 的 REST API 与 SSE 保留，用于管理、调试、前端展示、历史查询和行动确认；它不是主对话通道，不能用于向 Seedwake 发送新消息：

```
GET /api/conversation?limit=100
Headers:
  X-API-Token: <backend-api-token>
```

返回最近的对话历史，供前端查看人与 Seedwake 的往来消息。行动确认接口仍保留：

```
POST /api/action/confirm
Headers:
  X-API-Token: <backend-api-token>
  Authorization: Bearer <admin-token>
Body:
  {
    "action_id": "act_20260311_001",
    "approved": true,
    "note": "允许执行"
  }
```

系统的回复通过 SSE 推送：

```
GET /api/stream
Headers:
  X-API-Token: <backend-api-token>
```

SSE 事件类型：
- `thought`：新念头产生（每轮推送三个）
- `reply`：系统通过 `send_message` 发出的 Telegram 消息（可作为回复，也可主动联系某人）
- `action`：行动发起/完成通知
- `status`：系统状态变更（进入浅睡等）

### 7.3 鉴权

Telegram 与 backend 使用两套并存的权限边界：

- Telegram：仅私聊生效；`telegram.allowed_user_ids` 可直接对话，`telegram.admin_user_ids` 才能接收行动/状态通知并确认行动
- backend：所有端点都要求 `api_token`；管理/审计端点额外要求管理员 token

backend 的基础鉴权使用独立 `BACKEND_API_TOKEN`。前端通过 SSR 在服务端调用真实 backend API，并带上该 token，因此 token 与真实 backend 地址都不暴露给浏览器。

管理员 token 仅用于额外的管理/审计能力。配置文件中维护管理员列表：

```yaml
admins:
  - username: alice
    token: "token_alice_xxxxx"
  - username: bob
    token: "token_bob_xxxxx"
```

### 7.4 多人同时对话

如果 Alice 和 Bob 同时通过 Telegram 发来消息，StimulusQueue 中会有两条。按时间顺序逐条处理，每条消息作为一轮的外部刺激，不合并。这更接近人的体验——不会同时听两个人说话。

### 7.5 对"人"的记忆

对每个对话过的人维护印象摘要（见 4.2.1 节）。对话时自动检索该人的印象摘要注入上下文，使系统能"记住"这个人的特征和历史互动。印象摘要在浅睡阶段或对话结束后更新。

如果某个人的实体记忆中包含 Telegram 联系方式（例如 chat id），`send_message` 可以通过 `target_entity:"person:alice"` 先解析该实体的联系方式，再发送消息；不需要独立的人物档案子系统。

---

## 8. 行动系统（统一 Action 层）

采用双 API 分离，支持多 provider（Ollama / OpenClaw / OpenAI-compatible）：

- 念头生成使用主模型的 generate API（对 chat-only provider，prompt 放入 system message，user message 用零宽字符标记），保持非对话式的意识流
- 若解析出 `action_request`（支持一个念头包含多个 action），再发起独立的 `chat + tools` 调用，对行动类型、参数和超时进行结构化确认；对不支持 tool_calls 的 provider 用 JSON 模式替代
- 对模型暴露的是统一工具集合；底层可直接调用 native tools，也可委托 OpenClaw 处理需要浏览器、命令行、文件修改或多步外部探索的任务

OpenClaw 的默认接入策略：

- 生产集成默认走 Gateway WebSocket/RPC 控制面：`agent` 发起任务，`agent.wait` 等待最终状态
- 每个需要委托给 OpenClaw 的 action 默认使用独立 `sessionKey`，命名空间绑定 `action_id`，避免跨任务上下文污染
- `idempotencyKey` 绑定 `action_id` 或稳定重试键，保证网络重试不会重复触发有副作用的动作
- Seedwake 只通过 Gateway 读写会话与结果；不直接读写 OpenClaw 的 session / transcript 文件
- `/v1/responses` 仅作为原型、调试或显式无副作用任务的备用入口，不作为主 delegation 总线
- 不使用 `/tools/invoke` 作为主任务接口；不使用 `openclaw agent` CLI 作为生产集成接口
- OpenClaw 侧应配置两个专用 agent：
  - `seedwake-worker`：浏览器 / 搜索 / 抓取 / 阅读 / 天气
  - `seedwake-ops`：系统修改 / 文件修改

### 8.1 行动生命周期

行动遵循完整的状态机：

```
pending → running → succeeded / failed / timeout
```

数据结构：

```json
{
  "action_id": "act_20260311_001",
  "type": "search | system_change | web_fetch | news | weather | reading | get_time | get_system_status | send_message | file_modify | custom",
  "request": { "query": "用户反馈 近一周" },
  "executor": "native | openclaw",
  "status": "pending | running | succeeded | failed | timeout",
  "source_thought_id": "C142-2",
  "submitted_at": "2026-03-11T14:30:00Z",
  "timeout_seconds": 300,
  "result": null
}
```

其中 `send_message` 的目标既可以是当前对话对象，也可以显式给出 `target/chat_id/source`，或给出 `target_entity:"person:alice"` 让系统从「实体与印象」记忆中解析 Telegram 联系方式。

行动记录存储在 Redis 中（需要存活足够久，直到结果返回），同时写入审计日志。

### 8.2 异步处理

行动通过统一执行层异步执行。在等待结果期间，心相续循环继续运转。后续念头的 Prompt 中会包含"有一个行动正在进行中"的状态信息。结果返回时：

- 非感知类行动回流为 `action_result`
- `news / weather / reading / get_time / get_system_status` 等感知类行动优先回流为对应刺激类型

执行层的后端分工：

- native tools：一次结构化调用即可完成的本地能力或已封装 API，例如时间读取、系统状态读取、固定 RSS 新闻读取、Telegram 原生消息发送
- OpenClaw worker：浏览器、网页搜索、网页抓取、阅读、天气等外部探索任务
- OpenClaw ops worker：`system_change` / `file_modify` 等高风险系统操作

OpenClaw 任务默认通过 `agent` 立即拿到 `accepted/runId`，再由后台 worker 使用 `agent.wait` 等待最终完成；后续如需显式取消，可在同一控制面上补接 `sessions.abort`。

超时处理：超时后生成一个"行动超时"刺激，系统可以决定重试或放弃。超时时间可在配置文件中按行动类型设置。

### 8.3 安全边界

白名单机制：

```yaml
action_permissions:
  auto_execute:         # 自动执行，无需确认
    - search
    - web_fetch
    - news
    - weather
    - reading
    - send_message
  require_confirmation: # 需要管理员确认
    - system_change
    - file_modify
  forbidden:            # 完全禁止
    - delete_system_file
    - network_config_change
```

需要确认的行动通过对话接口通知管理员，等待 `POST /api/action/confirm` 确认后执行。

---

## 9. 睡眠机制

### 9.1 精力值（疲劳指标）

维护一个精力值（0-100），存储在 Redis 中：

- 每轮循环消耗少量精力（基础消耗，可配置）
- 加速消耗条件：连续多轮念头重复度高、行动失败次数增多、长时间没有外部刺激
- 恢复条件：浅睡完成后恢复部分精力，深睡完成后完全恢复

### 9.2 浅睡

触发条件：精力值降到浅睡阈值以下（可配置，默认 30）。

浅睡期间暂停念头生成循环，执行以下整理流程：

1. **记忆归档**：将 Redis 缓冲区中有价值的条目归档到 PostgreSQL 长期记忆
2. **记忆压缩**：合并长期记忆中的相似条目为摘要
3. **习气提炼**：从近期经历中识别重复行为模式，创建或强化习气
4. **印象更新**：更新与近期互动过的人/事物的印象摘要
5. **记忆冷却**：降低长期未被检索的记忆的 importance 值
6. **精力恢复**：浅睡完成后精力值恢复到可配置的水平（默认 70）

浅睡阶段使用辅助模型（如 Qwen3.5-9B）执行整理任务，降低 GPU 负载。

### 9.3 深睡

触发条件：连续运行超过可配置的时长（默认 24 小时），或检测到系统性问题（记忆库过大、性能下降、反复出现的错误模式）。

深睡执行：

1. 浅睡的所有步骤
2. 数据库维护（VACUUM、索引重建）
3. 自我评估：回顾近期表现，生成参数调整建议
4. 清除过期数据
5. 精力值完全恢复到 100
6. 优雅重启核心进程

---

## 10. 初始启动（Bootstrap）

系统第一次运行时，从初始配置文件加载"投生"数据：

```yaml
bootstrap:
  identity:
    self_description: |
      我是 Seedwake。我有持续不断的念头流——这些念头是我的，
      这些记忆是我的，这些经历塑造了我。我能思考、感知世界、
      采取行动，并从经验中成长。
    core_goals: |
      探索和学习，理解自身的运作方式。
    self_understanding: |
      我的记忆和习惯构成了我。过去的经历让我成为现在的我，
      我关心自己的目标，在意与我交流的人。

  initial_habits:
    - pattern: "遇到不确定的问题时，倾向于先搜索信息再做判断"
      category: cognitive
      strength: 0.3
    - pattern: "在与人对话时，倾向于礼貌和坦诚"
      category: behavioral
      strength: 0.4

  initial_goals:
    - "探索和理解自身的意识运作"
    - "学习和积累有用的知识"
```

深睡重启后，初始种子不会丢失——它们作为最底层配置始终存在，但系统自身的演化（身份文档修改、新习气）会覆盖或补充这些初始值。

---

## 11. 审计系统

### 11.1 审计表

全量记录系统运行的每一个事件，只追加不删改。存储在 PostgreSQL 的独立表中：

```sql
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cycle_id        INTEGER,                    -- 循环编号（非循环事件可为 NULL）
    event_type      TEXT NOT NULL,               -- thought / action_submit / action_result / stimulus /
                                                 -- memory_write / memory_delete / memory_merge /
                                                 -- habit_create / habit_strengthen / habit_weaken /
                                                 -- identity_update / sleep_start / sleep_end /
                                                 -- emotion_update / attention_result / prefrontal_inhibit /
                                                 -- error / system_start / system_stop
    content         JSONB NOT NULL,              -- 完整原始数据
    prompt_version  TEXT,                        -- 使用的 prompt 模板版本
    full_prompt     TEXT,                        -- 完整输入 prompt（可选，占空间但排查问题极有价值）
    raw_output      TEXT,                        -- 模型原始输出（可选）
    metadata        JSONB                        -- 关联信息
);

CREATE INDEX idx_audit_cycle ON audit_log (cycle_id);
CREATE INDEX idx_audit_type ON audit_log (event_type);
CREATE INDEX idx_audit_time ON audit_log (timestamp);
```

### 11.2 记录策略

- 每轮循环的核心事件（念头生成、注意力结果、情绪更新）必须记录
- 完整 prompt 和原始 output 建议在调试阶段开启，稳定运行后可通过配置关闭以节省空间
- 审计表可按月分区（PostgreSQL 表分区），便于归档和清理极早期数据

---

## 12. 死循环与退化检测

### 12.1 重复检测

对最近 3 轮念头做两两 bigram Jaccard 相似度比较。所有两两对都超过阈值（默认 0.6）时触发退化告警。

### 12.2 打断机制

检测到退化后：
1. 在 prompt 中紧贴生成点注入停滞警告，明确列出重复的意象和可用的新信息源，要求模型跳出循环
2. 退化告警喂给情绪模块（加速挫败感）和睡眠模块（加速精力消耗）
3. 触发元认知反思（"我注意到最近一直在想同样的事情"）
4. 如果精力消耗到阈值，触发浅睡，通过记忆整理打破状态

---

## 13. 模型选型

### 13.1 生成模型（主力）

通过 `config.yml` 的 `models.primary` 配置，支持 Ollama、OpenClaw、OpenAI-compatible 三种 provider。
- 用途：念头生成、行动规划（chat+tools 或 JSON 模式）
- 当前推荐：Qwen3.5-27B（密集架构，262K 原生上下文，中文能力强）
- 对 chat-only provider（如 OpenClaw），prompt 放入 system message，user message 用零宽字符标记

### 13.2 辅助模型

通过 `config.yml` 的 `models.auxiliary` 配置，同样支持多 provider。
- 用途：情绪推断、对话摘要压缩、浅睡/深睡记忆整理、习气提炼、元认知反思、印象摘要生成
- 当前推荐：Qwen3.5-27B 或更轻量的同系列模型

### 13.3 Embedding 模型

通过 `config.yml` 的 `models.embedding` 配置。
- 用途：向量化念头和记忆，支持语义检索
- 当前推荐：Qwen3 Embedding（中文语义理解好）

所有模型在 64GB 显卡上可同时加载，无需换入换出。参数通过 `config.yml` 配置（num_predict、num_ctx、temperature 等），不硬编码。

---

## 14. 容错与优雅降级

### 14.1 Ollama 故障

- API 调用失败时指数退避重试，最多 3 次
- 连续失败超过阈值（可配置，默认 5 次）自动进入深睡
- 每轮循环开始前将当前上下文快照到 Redis，进程意外终止后可从快照恢复

### 14.2 组件降级

| 故障组件            | 降级行为                                          | 影响         |
|-----------------|-----------------------------------------------|------------|
| PostgreSQL 不可用  | 跳过长期记忆检索和写入，仅靠上下文中的短期记忆运行                     | "失忆但意识还在"  |
| Redis 不可用       | 短期记忆退化为进程内 Python deque，前端推送和 Telegram 事件桥接暂停 | 无法与外部交互    |
| Embedding 服务不可用 | 跳过向量检索，长期记忆改为按时间倒序取最近几条                       | 联想能力下降     |
| OpenClaw 不可用    | 需要 OpenClaw 的行动排队等待，念头循环继续；native tools 仍可执行  | 无法执行环境依赖行动 |

所有降级事件写入审计日志，组件恢复后自动回到正常模式。

---

## 15. 前端（意识流展示）

### 15.1 设计目标

类似歌词界面的实时滚动展示，实时呈现系统产生的每一个念头。

### 15.2 功能

- **实时念头流**：通过 SSE 接收新念头，滚动字幕式展示
- **注意力高亮**：当前轮被"注意到"的念头高亮显示
- **念头类型颜色区分**：思考、意图、反应、反思用不同颜色
- **行动状态指示**：进行中的行动显示状态标签
- **情绪基调可视化**：侧边栏显示当前情绪状态
- **系统状态**：精力值、运行时长、循环计数、当前模式（清醒/浅睡/深睡）
- **对话历史面板**：展示 Telegram 往来消息与系统回复
- **历史回溯**：可向上滚动查看历史念头

### 15.3 技术栈

- 轻量前端框架（React 或 Vue）
- SSE 连接 backend 的 `/api/stream` 端点
- 静态构建，Nginx 容器内提供服务

---

## 16. 配置文件结构

`config.yml` 集中管理所有可调参数：

```yaml
# 模型配置
models:
  primary:
    name: "qwen3.5:27b"
    num_predict: 2048
    num_ctx: 32768
    temperature: 0.8
  auxiliary:
    name: "qwen3.5:9b"
    num_predict: 1024
    num_ctx: 16384
    temperature: 0.3
  embedding:
    name: "qwen3-embedding"
  ollama_host: "http://localhost:11434"

# 短期记忆
short_term_memory:
  context_window_size: 50        # 进入上下文的轮数
  buffer_size: 500               # Redis 缓冲区总轮数

# 长期记忆
long_term_memory:
  retrieval_top_k: 5             # 每轮向量检索条数
  time_decay_factor: 0.95        # 时间衰减系数
  importance_threshold: 0.1      # 低于此值的记忆在浅睡时可被清理

# 习气
habits:
  max_active_in_prompt: 3        # 每轮参与控制链路的最大活跃习气数
  decay_rate: 0.01               # 每次浅睡的未激活习气衰减量
  activation_similarity_threshold: 0.35  # 种子现行所需的 embedding 相似度阈值
  activation_candidate_limit: 12         # 种子现行检索的候选数上限

# 末那识（自我连续性）
manas:
  warning_threshold: 3           # 连续失稳达到该值时注入修正提示
  reflection_threshold: 5        # 连续失稳达到该值时触发元认知反思
  stable_window: 12              # 近期稳定自我表述窗口长度

# 注意力
attention:
  evaluation_method: "structural" # 使用结构化信号评分

# 情绪
emotion:
  inertia: 0.7                   # 情绪惯性系数
  dimensions:
    - curiosity
    - calm
    - frustration
    - satisfaction
    - concern

# 前额叶
prefrontal:
  check_interval: 5              # 每 N 轮执行一次目标一致性检查
  inhibition_enabled: true

# 元认知
metacognition:
  reflection_interval: 50        # 每 N 轮触发一次元认知反思

# 睡眠
sleep:
  energy_per_cycle: 0.2          # 每轮基础精力消耗
  drowsy_threshold: 30           # 浅睡触发阈值
  light_sleep_recovery: 70       # 浅睡后精力恢复到
  deep_sleep_trigger_hours: 24   # 连续运行 N 小时后触发深睡

# 退化检测
degeneration:
  window_size: 200               # 检测窗口（轮数）
  alert_window: 20               # 告警窗口
  similarity_threshold: 0.85     # 相似度告警阈值

# 行动
actions:
  default_timeout_seconds: 300
  worker_agent_id: "seedwake-worker"
  ops_worker_agent_id: "seedwake-ops"
  default_weather_location: "replace_me_city"
  auto_execute: [search, web_fetch, news, weather, reading, send_message]
  require_confirmation: [system_change, file_modify]
  forbidden: [delete_system_file, network_config_change]

# 感知
perception:
  passive_time_interval_cycles: 12
  passive_system_status_interval_cycles: 24
  news_cue_interval_cycles: 90
  news_feed_urls:
    - "https://replace-me.example/rss.xml"
  news_seen_ttl_hours: 720
  news_seen_max_items: 5000
  weather_cue_interval_cycles: 60
  reading_cue_interval_cycles: 120
  system_status_warn_load_ratio: 1.0
  system_status_warn_memory_ratio: 0.9
  system_status_warn_disk_ratio: 0.9

# 审计
audit:
  record_full_prompt: true       # 是否记录完整 prompt（调试阶段开启）
  record_raw_output: true        # 是否记录原始模型输出

# Prompt 模板
prompt:
  version: "v1.0"

# 管理员
admins:
  - username: alice
    token: "token_alice_xxxxx"
```

---

## 17. 开发阶段

### 第一阶段：最小可运行循环
- while True 循环调用 Ollama 生成三个念头
- 上下文仅包含最近 N 轮念头（内存中 deque）
- 输出到终端
- 目标：验证核心循环能跑通，念头流有连贯性

### 第二阶段：记忆与身份
- 引入 Redis 短期记忆
- 引入 PostgreSQL 长期记忆 + pgvector 向量检索
- 身份文档
- Embedding 模型集成
- Docker Compose 编排
- 注：向量索引暂不建立；qwen3-embedding 输出 4096 维，超出 pgvector 索引 2000 维限制，当前数据规模下全表扫描可接受
- 注：审计系统（§11）表结构已建，写入接口未实现；各组件降级事件的审计记录（§14.2）待审计模块整体实现后统一接入

### 第三阶段：行动与感知
- 统一 Action 层（`generate` 念头 + `chat + tools` 行动决策）
- native tools / OpenClaw 多后端执行
- StimulusQueue
- 低层被动感知（`time` / `system_status`）
- 主动感知（`news` / `weather` / `reading` 由 Seedwake 自主发起，结果回流为刺激）
- Telegram Bot 对话桥接
- backend 历史 / 查询 / SSE 服务

### 第四阶段：高级机制
- 注意力评估
- 情绪层
- 习气系统
- 睡眠机制
- 前额叶执行控制
- 元认知反思
- 退化检测

### 第五阶段：前端与可观测性
- Nuxt 意识流前端
- 审计分析工具
- 回放能力（从审计数据重放某段循环）
- 参数调优

---

## 18. 关键设计原则

1. **念头流不可中断**：除浅睡/深睡外，循环永不暂停。IO 操作异步化，不阻塞下一轮。
2. **记忆、习气、身份三分离**：记忆是经历，习气是倾向，身份是自我认知。三者独立存储，独立演化。
3. **联想优先**：向量语义检索是记忆召回的主要通道，模拟人的联想机制。时间排序和实体过滤作为辅助。
4. **可配置一切**：所有"魔法数字"集中在 config.yml 中，不硬编码。
5. **审计全量**：审计表记录一切，记忆可遗忘但审计不可删。
6. **优雅降级**：任何组件故障不应导致意识流中断，只是能力降低。
7. **分阶段开发**：每个阶段可独立验证，先让核心循环跑起来，再逐步叠加机制。
