# Seedwake Deployment

This is the complete local runbook. For project motivation and observed behaviors, see [concepts.md](./concepts.md).

## Deployment Shape

Seedwake is one host-side core process plus containerized dependencies.

| Component | How to run | Role |
|-----------|------------|------|
| core | host: `uv run python -m core.main` | Thought-stream main loop. |
| Redis | `docker compose up -d redis` | Event bus, short-term memory, action state. |
| PostgreSQL + pgvector | `docker compose up -d postgresql` | Long-term memory and vector search. |
| bot | `docker compose up -d bot` | Telegram channel and notifications. |
| backend | `docker compose up -d backend` | REST API and SSE stream. |
| frontend | `cd frontend && pnpm exec nuxt dev` | Nuxt observer UI. |

core / bot / backend communicate through Redis and PostgreSQL. The normal shape is bot / backend in Docker, core on the host. core reaches Redis via `localhost:6379`; containers use `redis:6379`.

## Requirements

Hardware:

- A GPU large enough for the primary model if using local Ollama. A 27B-class model typically needs at least 24 GB VRAM.
- If the primary model runs remotely and only embeddings run locally, local hardware requirements are much lower.

Software:

- Python, managed with `uv`
- Docker
- Node.js + pnpm for the Nuxt frontend (`frontend/mise.toml` pins the expected versions)
- Ollama or another OpenAI-compatible model endpoint
- OpenClaw for non-native actions
- Telegram Bot Token if you want external conversation

Only Linux has been verified.

## Startup Order

From scratch:

1. source + Python dependencies
2. `config.yml` + `.env`
3. Redis + PostgreSQL
4. models
5. OpenClaw / Telegram as needed
6. bot + backend
7. core
8. frontend

## 1. Fetch Sources and Install Python Dependencies

```bash
git clone <repo-url> seedwake
cd seedwake
uv sync
```

Use `uv run ...` for Python commands so the project interpreter and dependencies are used.

## 2. Prepare Configuration Files

```bash
# English bootstrap / logs / prompts
cp config.example.en.yml config.yml
cp .env.en.example .env

# Or Chinese
# cp config.example.zh.yml config.yml
# cp .env.zh.example .env
```

`config.yml` controls behavior. `.env` holds secrets and connection addresses.

## 3. Start Redis and PostgreSQL

Fill `.env`:

```bash
DB_HOST=localhost
DB_PORT=5432
DB_NAME=seedwake
DB_USER=seedwake
DB_PASSWORD=replace_me

REDIS_HOST=localhost
REDIS_PORT=6379
```

Start dependencies:

```bash
docker compose up -d postgresql redis
```

On first startup, `schema.sql` creates the database schema and enables `vector`. If you use a non-Docker PostgreSQL, run `schema.sql` manually and confirm `CREATE EXTENSION vector` succeeds.

## 4. Prepare Models

The `models` section in `config.yml` defines three model roles:

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

Providers:

- `ollama`: local or remote Ollama. Set `OLLAMA_BASE_URL` in `.env`; pull models first.
- `openclaw`: primary model through a remote OpenClaw HTTP proxy. Set `OPENCLAW_HTTP_BASE_URL` and `OPENCLAW_GATEWAY_TOKEN`.
- `openai_compatible`: any OpenAI-compatible endpoint. Set `OPENAI_COMPAT_BASE_URL` and `OPENAI_COMPAT_API_KEY`.

Keep the embedding model local unless you have a specific reason not to; embedding calls are frequent.

### Optional Camera Input

To enable passive visual input, add:

```yaml
perception:
  camera_stream_url: "http://localhost:8081"
```

core grabs one MJPEG frame before primary generation and passes it to the primary model. Only vision-capable primary models / variants support this. Non-vision models error directly; the image is not silently ignored.

## 5. Configure OpenClaw

OpenClaw executes non-native actions:

- `search`, `web_fetch`, `reading`, `weather`
- `file_modify`, `system_change`

Without OpenClaw, these actions may still be generated in thoughts, but they will not be dispatched; they leave `not_auto_execute` or failure traces.

### 5.1 Gateway Connection

In `.env`:

```bash
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789
OPENCLAW_GATEWAY_TOKEN=replace_me_gateway_token
OPENCLAW_HTTP_BASE_URL=http://127.0.0.1:18789
```

In `config.yml`:

```yaml
actions:
  use_openclaw_http_fallback: true
```

### 5.2 Device Identity

On first Gateway connection, Seedwake generates `data/openclaw/device.json` with an Ed25519 device identity. It contains a private key; do not commit or share it. The program attaches device identity and signature material during handshake, so manual public-key registration is normally not required.

You can inspect devices on the OpenClaw machine:

```bash
openclaw devices list
```

Manual registration is only needed if the Gateway is configured with an additional whitelist / approval policy.

### 5.3 Worker Agents

In `config.yml`:

```yaml
actions:
  worker_agent_id: "seedwake-worker"
  ops_worker_agent_id: "seedwake-ops"
  session_key_prefix: "seedwake:action"
```

Create agents on the OpenClaw machine:

```bash
openclaw agents add seedwake-worker \
  --workspace ~/.openclaw/workspace-seedwake-worker \
  --non-interactive

openclaw agents add seedwake-ops \
  --workspace ~/.openclaw/workspace-seedwake-ops \
  --non-interactive
```

Inspect actual indices:

```bash
openclaw agents list --json
openclaw config get agents.list
```

The indices below are examples. Replace `[1]` and `[2]` with the actual positions of `seedwake-worker` and `seedwake-ops`:

```bash
openclaw config set 'agents.list[1].tools.profile' minimal
openclaw config set 'agents.list[1].tools.alsoAllow' '["browser","web_fetch","web_search"]' --strict-json
openclaw config set 'agents.list[2].tools.profile' coding
```

The regular worker should have web access but no local ops powers. The ops worker should have local file / command powers and does not need web access.

Enable the OpenAI-compatible HTTP endpoint:

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true
openclaw config set gateway.http.endpoints.chatCompletions.scopes '["operator.read","operator.write"]'
openclaw config set session.maintenance.mode "7d"
openclaw gateway restart
openclaw config get gateway.http.endpoints.chatCompletions
openclaw models status
```

Seedwake isolates each action with `agent:<worker_agent_id>:<session_key_prefix>:<action_id>`, so OpenClaw must preserve session-key isolation.

If you do not yet have an ops worker, set `ops_worker_agent_id` to the same value as `worker_agent_id`. If you have no OpenClaw at all, leave both IDs empty and remove `search`, `web_fetch`, `reading`, `weather` from `actions.auto_execute`.

## 6. Configure Telegram

Without Telegram, the system still runs but has no external conversation channel.

In `.env`:

```bash
TELEGRAM_BOT_TOKEN=123456:replace_me
BACKEND_API_TOKEN=replace_me_backend_token
```

In `config.yml`:

```yaml
telegram:
  allowed_user_ids: [123456789]
  admin_user_ids: [123456789]
  notification_channel_id: -1001234567890  # optional
```

## 7. Configure Action Policy

Common `actions` settings:

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

Keep `file_modify` and `system_change` behind confirmation unless you deliberately want autonomous ops.

## 8. Other Common Config

Useful sections:

- `runtime.cycle_interval_seconds`: delay between cycles.
- `runtime.context_window_cycles`: recent cycle window for prompt construction.
- `runtime.logging`: log directory and prompt log path.
- `sleep`: light / deep sleep thresholds.
- `memory`: short-term window and long-term recall settings.
- `emotion`: emotion inference and decay.
- `perception`: time / system / camera cues.

## 9. First-Run Sanity Check

```bash
uv run python -m unittest discover -s tests
```

The tests are local and do not require Redis / PostgreSQL / Ollama / OpenClaw / Telegram.

## 10. Start Bot and Backend

```bash
docker compose up -d bot
docker compose up -d backend
docker compose ps
```

backend is required by the frontend. bot is recommended for external conversation.

## 11. Start Core

```bash
uv run python -m core.main
```

On startup, core loads config, connects to Redis / PostgreSQL, prints model and connection status, then enters the thought loop.

Each cycle writes logs under `data/logs/`, with a short colored form printed to the terminal.

## 12. Start Frontend

```bash
cd frontend
cp .env.example .env
# Edit .env:
# NUXT_BACKEND_BASE_URL=http://127.0.0.1:8000
# NUXT_BACKEND_API_TOKEN=<same value as BACKEND_API_TOKEN>
# NUXT_PUBLIC_LANGUAGE=zh   # or en; keep it aligned with config.yml language

corepack enable
corepack prepare pnpm@9.3.0 --activate
pnpm install
pnpm exec nuxt dev --host 127.0.0.1 --port 3000
```

Open `http://127.0.0.1:3000`.

Production build:

```bash
pnpm build
pnpm preview --host 127.0.0.1 --port 3000
```

The frontend calls backend through `/api/seed/*`; Nuxt injects `X-API-Token` server-side, so the browser does not receive `BACKEND_API_TOKEN`.

## 13. Shutdown

- core: `Ctrl+C`. Do not use `kill -9`; it can lose unpersisted short-term state.
- frontend: `Ctrl+C`.
- containers: `docker compose down`. Do not pass `-v` unless you want to delete data volumes.
