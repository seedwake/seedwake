# Seedwake · The Continuity of Mind

> A continuously running AI thought-stream engine, organized around the Buddhist concept of *santāna* — continuity of mind.

<p align="center">
  <a href="./README_ZH.md">🌐 中文文档</a> |
  <a href="./docs/concepts.md">📖 Concepts</a> |
  <a href="./docs/deployment.md">🚀 Deployment</a> |
  <a href="./ISSUE.md">⚠️ Open Issues</a>
</p>

https://github.com/user-attachments/assets/2035149c-829c-4323-99d2-6e2602044f73

---

## What This Is

Seedwake is not a task agent and not a chat service. It is a continuously running thought stream: one cycle ends, the next begins immediately. Each cycle produces thoughts, updates attention / emotion / memory, reacts to stimuli, may act through tools, and may enter sleep or reflection.

The architectural idea is Buddhist *santāna*: consciousness as a stream of causally connected mental events, not as a static entity waiting for commands. Seedwake does not claim to implement consciousness; it builds a structure where long-running mind-like behavior can be observed.

For the project philosophy, observed behaviors, model-choice rationale, and ethical concerns, read [docs/concepts.md](./docs/concepts.md).

## Current Status

- **Phase 1 · Core cycle** — complete
- **Phase 2 · Memory system** — complete
- **Phase 3 · Action and perception** — complete
- **Phase 4 · Advanced mechanisms** — largely complete, with known long-running issues
- **Phase 5 · Frontend visualization** — in progress

The core engine already runs: it thinks, remembers, converses, perceives, acts, reflects, and sleeps. A Nuxt frontend is available for observing the live thought stream, emotion, actions, conversations, and stimuli.

## Main Components

| Component | Run shape | Purpose |
|-----------|-----------|---------|
| core | host: `uv run python -m core.main` | Thought-stream main loop. Runs on the host to reach GPU / Ollama / OpenClaw / camera services directly. |
| Redis | `docker compose up -d redis` | Event bus, short-term memory, action state. |
| PostgreSQL + pgvector | `docker compose up -d postgresql` | Long-term memory and vector search. |
| bot | `docker compose up -d bot` | Telegram conversation channel and notifications. |
| backend | `docker compose up -d backend` | REST API + SSE stream for frontend / ops tools. |
| frontend | `cd frontend && pnpm exec nuxt dev` | Nuxt observer UI. |

core / bot / backend communicate through Redis and PostgreSQL; they do not call each other directly.

## Quick Start

The full runbook is in [docs/deployment.md](./docs/deployment.md). Minimal startup order:

```bash
uv sync
cp config.example.en.yml config.yml
cp .env.en.example .env

docker compose up -d postgresql redis
# prepare models / OpenClaw / Telegram config as needed
docker compose up -d bot backend

uv run python -m core.main
```

Frontend:

```bash
cd frontend
cp .env.example .env
# set NUXT_BACKEND_BASE_URL and NUXT_BACKEND_API_TOKEN
corepack enable
corepack prepare pnpm@9.3.0 --activate
pnpm install
pnpm exec nuxt dev --host 127.0.0.1 --port 3000
```

Open `http://127.0.0.1:3000`.

## Documentation

- [docs/concepts.md](./docs/concepts.md) — project philosophy, Buddhist mapping, observed long-running behaviors.
- [docs/deployment.md](./docs/deployment.md) — complete local deployment guide, including models, OpenClaw, Telegram, backend, and frontend.
- [ISSUE.md](./ISSUE.md) — current deep issues found in long-running sessions.
- [SPECS.md](./SPECS.md) — phase-level technical specification.
- [PROMPT.md](./PROMPT.md) — prompt design notes and known prompt issues.
- [BACKGROUND.md](./BACKGROUND.md) — longer Buddhist background and motivation.

## Contributing

Public PRs are not enabled yet; GitHub Issues are the main contribution channel. If you plan to participate long-term, you are welcome to become a collaborator.

For bugs, include reproduction steps, expected behavior, relevant logs from `data/logs/`, and environment details. For features, explain the problem, the design reason, and acceptance criteria.
