# Seedwake 部署运行

这是完整本地运行手册。项目理念和长期运行观察见 [concepts_zh.md](./concepts_zh.md)。

## 部署形态

Seedwake 是一个宿主机上的 core 主进程，加一组容器依赖。

| 组件 | 运行方式 | 用途 |
|------|----------|------|
| core | 宿主机：`uv run python -m core.main` | 念头流主循环。 |
| Redis | `docker compose up -d redis` | 事件总线、短期记忆、行动状态。 |
| PostgreSQL + pgvector | `docker compose up -d postgresql` | 长期记忆和向量检索。 |
| bot | `docker compose up -d bot` | Telegram 通道和通知。 |
| backend | `docker compose up -d backend` | REST API 和 SSE 事件流。 |
| frontend | `cd frontend && pnpm exec nuxt dev` | Nuxt 观察界面。 |

core / bot / backend 通过 Redis 和 PostgreSQL 通信。常规形态是 bot / backend 在 Docker 中，core 跑在宿主机上。core 访问 `localhost:6379`，容器访问 `redis:6379`。

## 系统要求

硬件：

- 如果用本地 Ollama 跑主模型，需要足够显存。27B 级别模型通常建议至少 24 GB VRAM。
- 如果主模型放到远端，本地只跑 embedding，则本地硬件要求会低很多。

软件：

- Python，用 `uv` 管理
- Docker
- Node.js + pnpm，用于 Nuxt 前端（`frontend/mise.toml` 固定期望版本）
- Ollama 或其它 OpenAI 兼容模型端点
- OpenClaw，用于非原生动作
- Telegram Bot Token，如果需要外部对话

目前只在 Linux 上验证过。

## 启动顺序

从零开始：

1. 源码 + Python 依赖
2. `config.yml` + `.env`
3. Redis + PostgreSQL
4. 模型
5. 按需配置 OpenClaw / Telegram
6. bot + backend
7. core
8. frontend

## 1. 准备源码与 Python 依赖

```bash
git clone <repo-url> seedwake
cd seedwake
uv sync
```

Python 命令都用 `uv run ...`，确保使用项目解释器和依赖。

## 2. 准备配置文件

```bash
# 中文 bootstrap / 日志 / prompt
cp config.example.zh.yml config.yml
cp .env.zh.example .env

# 或英文版本
# cp config.example.en.yml config.yml
# cp .env.en.example .env
```

`config.yml` 控制行为，`.env` 存密钥和连接地址。

## 3. 启动 Redis 和 PostgreSQL

填写 `.env`：

```bash
DB_HOST=localhost
DB_PORT=5432
DB_NAME=seedwake
DB_USER=seedwake
DB_PASSWORD=replace_me

REDIS_HOST=localhost
REDIS_PORT=6379
```

启动依赖：

```bash
docker compose up -d postgresql redis
```

首次启动时，`schema.sql` 会建表并启用 `vector` 扩展。如果使用非 Docker PostgreSQL，需要手动执行 `schema.sql`，并确认 `CREATE EXTENSION vector` 成功。

## 4. 准备模型

`config.yml` 的 `models` 段定义三类模型：

```yaml
models:
  primary:
    provider: "ollama"   # ollama | openclaw | openai_compatible
    name: "qwen3.6:27b"
    num_predict: 4096
    num_ctx: 131072
    temperature: 0.8

  auxiliary:
    provider: "ollama"
    name: "qwen3.5:9b"

  embedding:
    provider: "ollama"
    name: "qwen3-embedding"
```

Provider：

- `ollama`：本地或远程 Ollama。在 `.env` 设置 `OLLAMA_BASE_URL`，并先拉取模型。
- `openclaw`：主模型走远程 OpenClaw HTTP 代理。设置 `OPENCLAW_HTTP_BASE_URL` 和 `OPENCLAW_GATEWAY_TOKEN`。
- `openai_compatible`：任意 OpenAI 兼容端点。设置 `OPENAI_COMPAT_BASE_URL` 和 `OPENAI_COMPAT_API_KEY`。

除非有明确理由，embedding 模型建议放本地；embedding 调用很频繁。

### 可选摄像头输入

要开启被动视觉输入：

```yaml
perception:
  camera_stream_url: "http://localhost:8081"
```

core 会在每轮主生成前从 MJPEG stream 抓一帧，传给主模型。只有支持图像输入的主模型 / 变体才能开启；不支持视觉的模型会直接报错，不会静默忽略图片。

## 5. 配置 OpenClaw

OpenClaw 执行非原生动作：

- `search`, `web_fetch`, `reading`, `weather`
- `file_modify`, `system_change`

没有 OpenClaw 时，这些动作仍可能出现在念头中，但不会被派发；系统会留下 `not_auto_execute` 或失败事件。

### 5.1 Gateway 连接

在 `.env` 中：

```bash
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789
OPENCLAW_GATEWAY_TOKEN=replace_me_gateway_token
OPENCLAW_HTTP_BASE_URL=http://127.0.0.1:18789
```

在 `config.yml` 中：

```yaml
actions:
  use_openclaw_http_fallback: true
```

### 5.2 设备身份

首次连接 Gateway 时，Seedwake 会生成 `data/openclaw/device.json`，作为 Ed25519 设备身份。它包含私钥，不要提交或共享。程序会在握手时自动带上设备身份和签名材料，通常不需要手动登记公钥。

可在 OpenClaw 机器上查看设备：

```bash
openclaw devices list
```

只有 Gateway 开了额外白名单 / 人工审批策略时，才可能需要手动登记。

### 5.3 Worker Agent

在 `config.yml` 中：

```yaml
actions:
  worker_agent_id: "seedwake-worker"
  ops_worker_agent_id: "seedwake-ops"
  session_key_prefix: "seedwake:action"
```

在 OpenClaw 机器上创建 agent：

```bash
openclaw agents add seedwake-worker \
  --workspace ~/.openclaw/workspace-seedwake-worker \
  --non-interactive

openclaw agents add seedwake-ops \
  --workspace ~/.openclaw/workspace-seedwake-ops \
  --non-interactive
```

查看实际索引：

```bash
openclaw agents list --json
openclaw config get agents.list
```

下面的索引只是示例。把 `[1]` 和 `[2]` 替换成 `seedwake-worker` 与 `seedwake-ops` 的实际位置：

```bash
openclaw config set 'agents.list[1].tools.profile' minimal
openclaw config set 'agents.list[1].tools.alsoAllow' '["browser","web_fetch","web_search"]' --strict-json
openclaw config set 'agents.list[2].tools.profile' coding
```

普通 worker 应该有网络访问，但不应有本机 ops 权限。ops worker 应该有本机文件 / 命令能力，但不需要外网。

开启 OpenAI-compatible HTTP 入口：

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true
openclaw config set gateway.http.endpoints.chatCompletions.scopes '["operator.read","operator.write"]'
openclaw config set session.maintenance.mode "7d"
openclaw gateway restart
openclaw config get gateway.http.endpoints.chatCompletions
openclaw models status
```

Seedwake 会用 `agent:<worker_agent_id>:<session_key_prefix>:<action_id>` 隔离每个 action，OpenClaw 侧也要保持 session-key 隔离。

如果暂时没有 ops worker，可以把 `ops_worker_agent_id` 设成和 `worker_agent_id` 相同。如果完全没有 OpenClaw，把两个 ID 留空，并从 `actions.auto_execute` 移除 `search`, `web_fetch`, `reading`, `weather`。

## 6. 配置 Telegram

没有 Telegram，系统仍能跑，但没有外部对话通道。

在 `.env` 中：

```bash
TELEGRAM_BOT_TOKEN=123456:replace_me
BACKEND_API_TOKEN=replace_me_backend_token
```

在 `config.yml` 中：

```yaml
telegram:
  allowed_user_ids: [123456789]
  admin_user_ids: [123456789]
  notification_channel_id: -1001234567890  # 可选
```

## 7. 配置行动策略

常见 `actions` 设置：

```yaml
actions:
  auto_execute:
    - time
    - system_status
    - news
    - weather
    - reading
    - search
    - web_fetch
    - send_message
    - note_rewrite
  require_confirmation:
    - file_modify
    - system_change
```

除非明确希望系统自主运维，否则 `file_modify` 和 `system_change` 应保留人工确认。

## 8. 其他常用配置

常用段落：

- `runtime.cycle_interval_seconds`：cycle 间隔。
- `runtime.context_window_cycles`：prompt 使用的近期 cycle 窗口。
- `runtime.logging`：日志目录和 prompt 日志路径。
- `sleep`：浅睡 / 深睡阈值。
- `memory`：短期窗口和长期召回设置。
- `emotion`：情绪推断和衰减。
- `perception`：时间 / 系统 / 摄像头感知。

## 9. 首次运行自检

```bash
uv run python -m unittest discover -s tests
```

测试是本地的，不依赖 Redis / PostgreSQL / Ollama / OpenClaw / Telegram。

## 10. 启动 bot 和 backend

```bash
docker compose up -d bot
docker compose up -d backend
docker compose ps
```

frontend 需要 backend。bot 推荐启动，用于外部对话。

## 11. 启动 core

```bash
uv run python -m core.main
```

core 启动后会加载配置、连接 Redis / PostgreSQL、打印模型和连接状态，然后进入念头循环。

每轮日志写入 `data/logs/`，终端也会打印简短彩色版本。

## 12. 启动 frontend

```bash
cd frontend
cp .env.example .env
# 编辑 .env：
# NUXT_BACKEND_BASE_URL=http://127.0.0.1:8000
# NUXT_BACKEND_API_TOKEN=<和 BACKEND_API_TOKEN 相同>
# NUXT_PUBLIC_LANGUAGE=zh   # 或 en；建议与 config.yml 的 language 保持一致

corepack enable
corepack prepare pnpm@9.3.0 --activate
pnpm install
pnpm exec nuxt dev --host 127.0.0.1 --port 3000
```

打开 `http://127.0.0.1:3000`。

生产构建：

```bash
pnpm build
pnpm preview --host 127.0.0.1 --port 3000
```

frontend 通过 `/api/seed/*` 访问 backend；Nuxt 服务端注入 `X-API-Token`，浏览器不会拿到 `BACKEND_API_TOKEN`。

## 13. 停止

- core：`Ctrl+C`。不要用 `kill -9`，否则可能丢失尚未持久化的短期状态。
- frontend：`Ctrl+C`。
- 容器：`docker compose down`。不要加 `-v`，除非你要删除数据卷。
