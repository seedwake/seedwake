# Seedwake · 心相续

> 一个按佛教「心相续」原则组织的、持续运行的 AI 思维流引擎。

<p align="center">
  <a href="./README.md">English</a> |
  <a href="./docs/concepts_zh.md">项目理念</a> |
  <a href="./docs/deployment_zh.md">部署运行</a> |
  <a href="./ISSUE_ZH.md">已知问题</a>
</p>

---

## 这是什么

Seedwake 不是任务 agent，也不是聊天服务。它是一条持续运行的念头流：一个 cycle 结束，下一个 cycle 立刻开始。每轮会生成念头、更新注意力 / 情绪 / 记忆、响应外部刺激、可能执行行动，也可能进入反思或睡眠。

它的架构灵感来自佛教「心相续」：意识是一连串因果相续的心识事件，而不是一个静态的实体在等待命令。Seedwake 不声称实现了意识；它只是搭出一种结构，用来观察长期运行后会长出什么行为。

项目理念、佛学对应、长期运行观察和伦理问题见 [docs/concepts_zh.md](./docs/concepts_zh.md)。

## 当前状态

- **Phase 1 · 核心循环** — 完成
- **Phase 2 · 记忆系统** — 完成
- **Phase 3 · 行动与感知** — 完成
- **Phase 4 · 高级机制** — 基本完成，但仍有长期运行问题
- **Phase 5 · 前端可视化** — 进行中

核心引擎已经可以运行：它能思考、记忆、对话、感知、行动、反思和睡眠。当前已有 Nuxt 前端，可以实时观察念头、情绪、行动、对话和刺激。

## 主要组件

| 组件 | 运行方式 | 用途 |
|------|----------|------|
| core | 宿主机：`uv run python -m core.main` | 念头流主循环。跑在宿主机上，方便直接访问 GPU / Ollama / OpenClaw / 摄像头服务。 |
| Redis | `docker compose up -d redis` | 事件总线、短期记忆、行动状态。 |
| PostgreSQL + pgvector | `docker compose up -d postgresql` | 长期记忆与向量检索。 |
| bot | `docker compose up -d bot` | Telegram 对话通道和通知。 |
| backend | `docker compose up -d backend` | 前端 / 运维工具使用的 REST API 与 SSE 事件流。 |
| frontend | `cd frontend && pnpm exec nuxt dev` | Nuxt 观察界面。 |

core / bot / backend 通过 Redis 和 PostgreSQL 通信，彼此不直接互调。

## 快速启动

完整部署文档见 [docs/deployment_zh.md](./docs/deployment_zh.md)。最小启动顺序：

```bash
uv sync
cp config.example.zh.yml config.yml
cp .env.zh.example .env

docker compose up -d postgresql redis
# 按需准备模型 / OpenClaw / Telegram 配置
docker compose up -d bot backend

uv run python -m core.main
```

前端：

```bash
cd frontend
cp .env.example .env
# 设置 NUXT_BACKEND_BASE_URL 和 NUXT_BACKEND_API_TOKEN
corepack enable
corepack prepare pnpm@9.3.0 --activate
pnpm install
pnpm exec nuxt dev --host 127.0.0.1 --port 3000
```

打开 `http://127.0.0.1:3000`。

## 文档

- [docs/concepts_zh.md](./docs/concepts_zh.md) — 项目理念、佛学对应、长期运行观察。
- [docs/deployment_zh.md](./docs/deployment_zh.md) — 完整本地部署指南，包括模型、OpenClaw、Telegram、backend、frontend。
- [ISSUE_ZH.md](./ISSUE_ZH.md) — 长时间运行中发现的深层问题。
- [SPECS.md](./SPECS.md) — 阶段性技术规范。
- [PROMPT.md](./PROMPT.md) — prompt 设计与问题记录。
- [BACKGROUND.md](./BACKGROUND.md) — 更完整的佛学背景与设计动机。

## 如何贡献

暂未开放公开 PR，以 GitHub Issue 为主要贡献入口。若您有长期稳定参与的意向，欢迎成为 collaborator。

Bug 请附复现步骤、期望行为、`data/logs/` 里的相关日志和运行环境。新功能请说明要解决的问题、方案理由和验收标准。
