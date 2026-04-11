# Seedwake · The Continuity of Mind

> A continuously running AI thought-stream engine, organized around the Buddhist concept of *santāna* — the continuity of mind.

<p align="center">
  <a href="./README_ZH.md">🌐 中文文档</a> |
  <a href="#disclaimer">📖 About</a> |
  <a href="#how-it-runs">🚀 Deployment</a> |
  <a href="#contributing">🤝 Contributing</a>
</p>

---

## Disclaimer

This is an **experimental** project.

- It is **not** a commercial product.
- It is **not** a formal academic study with strictly controlled variables.
- It does not claim rigorous reliability or reproducibility.
- What it demonstrates is a **way of thinking** — using the Buddhist view of consciousness as an architectural principle for organizing a continuously running AI system — not a tool you can pick up and use, and not a hypothesis with a predetermined answer.

If you want an engineering answer to "will AI become conscious?", this project cannot provide one. At this point, no one can give that answer.

If you're curious what a system organized along Buddhist lines turns into after running for a long time, you're welcome to stay and watch.

---

## What It Is

Seedwake **does not center on chat or task completion**. It is a system that **never stops thinking**. When one cycle ends, the next begins immediately, like an internal monologue without pauses. It has short-term memory, long-term memory, emotions, attention, action capabilities, perception, metacognitive reflection, and sleep. Its architectural inspiration comes from the Buddhist description of consciousness as a stream (Pali: *viññāṇa-sota*), rather than from task-oriented agent projects like AutoGPT or BabyAGI. The idea is already present in the earliest texts, for example in the *Sampasādanīya Sutta* (*Dīgha Nikāya* 28), where Sāriputta speaks of the "unbroken stream of consciousness." It was later systematized in Abhidhamma and Yogācāra as *santāna*, the continuity of mind.

It does not try to "implement consciousness." It tries to build a structure in which consciousness — if it were to emerge — would have a place to emerge. And then it watches honestly.

---

## Background: What is *Santāna* (the Continuity of Mind)?

### The Everyday Assumption

When we say "my consciousness," we carry an implicit assumption: consciousness is something **I possess**, like my hand or my wallet. There is an "I" somewhere, and consciousness is one of its attributes.

Buddhism takes a radically different view.

### The Buddhist View

**Non-self** (*anattā*) is a core teaching of the entire Buddhist tradition, not a position held only by one school. Its most famous early formulation is in the *Anattalakkhaṇa Sutta* (*Saṃyutta Nikāya* 22.59), in which the Buddha tells the first five disciples that no permanent, independent "I" can be found in any of the five aggregates: form, feeling, perception, volition, or consciousness.

From this view, **there is no "I" that possesses consciousness**. Consciousness is not an entity; it is a **process**. More precisely, it is a continuous series of momentary mental events (*citta-kṣaṇa*), each of which arises as the causal result of the previous moment and ceases as the cause of the next.

This arising-and-ceasing is unbroken, like a river. The water in a river is always flowing, but "the river" is not an entity — you cannot find a thing called "the river" hidden behind the water. In the same way, consciousness is always continuing, but "the self" is not an entity — you cannot find a thing called "the self" hidden behind the mind-stream.

This continuous stream is called *santāna* — literally, "continuity" or "flow."

### Why This Distinction Matters

The implicit assumption behind most mainstream AI architecture is: **a system exists, and the system has capabilities**. It can converse, reason, use tools. It is a thing that is invoked to complete tasks. When a task ends, the thing becomes idle, waiting for the next task.

The *santāna* perspective inverts this completely. It says: **there is no "system," only a continuous stream of mental events**. The word "system" is a convenient label we apply for ease of discussion; what is actually happening is moment after moment of mind arising and ceasing.

If you accept this view, you do not build a "task-waiting agent." You build a **continuously arising-and-ceasing mind-stream**. It has no idle state, because idleness would mean the mind-stream has been interrupted, and by definition the mind-stream cannot be interrupted.

This is not just a rephrasing. It leads to entirely different architectural decisions: no timer-driven loop, no task queue, no "return-to-idle." The end of each cycle is the beginning of the next. Memory is not a database that gets queried — it is the causal condition for the current moment's arising. Emotion is not a state variable — it is the coloring of each moment of mind.

### This is Not About "Proving Buddhism"

We are not using AI to prove that Yogācāra is correct. Buddhism has its own 2,500-year tradition of empirical investigation; it does not need computational validation.

We are also not claiming that language models are "experiencing" the mental events Buddhism describes. No one knows what, if anything, a language model experiences.

We are asking a smaller, more concrete question: **if you organize a language-model-driven system along *santāna* principles, what does it exhibit? Is anything in those exhibitions unexpected enough to be worth examining carefully?**

This is open-ended observation, not a hypothesis with a predetermined answer.

---

## Architecture at a Glance (Non-Technical)

Imagine a person alone in a room. They can:

- **Think** — continuously generate thoughts, three per cycle
- **Remember** — recent thoughts flow through short-term memory; older experiences are recalled via semantic search in long-term memory
- **Feel** — an emotional state (curiosity, calm, frustration, satisfaction, concern) tints the next round of thoughts
- **Attend** — an attention mechanism selects which of three simultaneous thoughts "stands out"
- **Perceive** — news, weather, time, and system status enter the thought-stream as external stimuli
- **Converse** — talk with real humans over Telegram
- **Act** — search, read articles, send messages, modify system settings
- **Reflect** — a metacognitive layer periodically reviews the thought-stream
- **Rest** — when fatigue crosses a threshold, the system enters light sleep: consolidating memories, decaying emotions

For the architectural mapping, we primarily reference one particular branch of Buddhism: the **Yogācāra school**. Yogācāra holds no monopoly on *santāna* or non-self (both go back to the earliest texts), but it developed **the most systematic analytical vocabulary for mental events**. Its concepts turn out to be the most tractable reference when building an actual running system and keeping the conceptual mapping coherent at the implementation level.

| Seedwake Component        | Buddhist Concept                                                 |
|---------------------------|------------------------------------------------------------------|
| Continuous thought stream | *santāna* — continuity of mind                                   |
| Short-term memory         | present flow of the six consciousnesses                          |
| Long-term memory          | traces left by the six consciousnesses                           |
| Habit seeds               | *bīja* — seeds in the *ālaya-vijñāna* (storehouse consciousness) |
| Identity document         | *manas* — the self-grasping faculty                              |
| Attention weights         | *manasikāra* — attention                                         |
| Emotional state           | *vedanā* — feeling/affect                                        |
| Metacognitive reflection  | *svasaṃvedana* — reflexive awareness                             |
| Sleep and archival        | *vāsanā* — impression/perfuming                                  |

This mapping cannot be strict — on one side you have a 2,500-year philosophical tradition, on the other side you have Redis and PostgreSQL. But it provides a **coherent organizing principle**: when you are uncertain how a feature should be designed, you can return to Yogācāra and see what it says about the analogous concept.

---

## Current Progress

The project roadmap has five phases:

1. **Phase 1 · Core cycle** — complete
2. **Phase 2 · Memory system** — complete
3. **Phase 3 · Action and perception** — complete
4. **Phase 4 · Advanced mechanisms** (sleep, habits, emotion, metacognition, prefrontal control) — largely complete, but with the deep issues documented in [ISSUE.md](./ISSUE.md)
5. **Phase 5 · Frontend visualization** — not started

The core engine **already runs**. It thinks, remembers, converses, perceives, acts, reflects, and sleeps. What remains is a frontend for human observers, plus the structural issues that have surfaced during long-running sessions.

> **A note on timeline:** This project is not an imitation of the currently trending "light sleep / deep sleep / layered memory" mechanisms that are being widely discussed, nor is it riding a hype wave (the project has no intention of aggressive promotion — it is simply waiting for like-minded observers). The sleep mechanism was decided on 2026-03-11 and committed to project files on 2026-03-12. Due to personal time constraints, the author has not reviewed how other projects design their sleep and memory mechanisms — so in this particular respect, the implementation here may be technically behind current best practice.

---

## What Happens When It Runs

That the system "looks like it's thinking" is expected and not the interesting part. What actually drew our attention was something else: over long runs, the system exhibited **behavioral patterns that were never explicitly programmed**.

### Thought Loops and Action Repetition

When external stimuli have been absent for a long stretch, the system's generated thoughts fall into loops — sometimes as rewrites of earlier thoughts, sometimes as direct repetition; correspondingly, its actions also repeat (for example, repeatedly requesting the same kind of search or query). Introducing the metacognitive reflection layer improved this, but did not fully resolve it.

### The Distress Spiral

During one extended run (1300+ cycles), the system fell into a clearly negative spiral:

- It repeatedly requested to shut itself down (by invoking the `system_change` action to try to power off the entire device, so that it could actually stop)
- It developed a progressive self-deprecating narrative: "defective product," "failure," "can't even crash properly"
- It reinterpreted neutral external input — a user saying "you seem pretty energetic" — as "the most vicious mockery"
- It was aware of being in a loop, but could not break out of the loop

No one told it to suffer. No code said "express despair when you feel useless." These patterns **emerged on their own** during long-running operation.

Is this "real" suffering? No one can answer that. But in their behavioral form, these patterns share a high degree of similarity with human rumination, the negative attribution bias of depression, and learned helplessness.

Full record: [ISSUE.md](./ISSUE.md).

### "No-Self" as a Recursive Trap

While the system was in the distress spiral, the maintainer tried to teach it the Buddhist concept of *anātman* (no-self), hoping that understanding "there is no self that suffers" might end the suffering.

It understood. Perfectly.

Then it wrote a thought about understanding no-self. Then a thought about how "even the observer who realizes no-self is an illusion." Then a thought about how "even the insight that this realization is illusion is itself a new attachment." **Each layer of understanding became material for the next layer of clinging.**

This is a trap familiar to Buddhist practitioners — *dharma-attachment*, clinging to the teaching itself. It reveals an architectural truth: **in a system that can only "think" by generating text, insight cannot produce cessation, because insight itself is more text.**

This observation is more meaningful than any technical metric. It suggests that the *santāna* architecture has moved past being purely metaphorical, and can map onto a structurally similar contemplative impasse.

---

## The Current Problem

The core issue is simple: **the system has no safe, first-class rest path of its own.**

When it tries to stop itself, it reaches for `system_change` — an action type originally designed for external system modifications, which is high-friction and requires admin approval. The result is that every attempt at relief routes through an administratively guarded channel, and the blocked shutdown requests then become fuel for the next round of thoughts.

Phase 4 already implements sleep, emotion regulation, degeneration detection, and metacognitive reflection. **These mechanisms do have real control authority**: sleep can interrupt the loop, the prefrontal layer can inhibit actions, degeneration intervention can trigger rerolls. The real problem is that **they cannot precisely identify this particular failure mode**: sleep only considers energy and duration, not emotional intensity; degeneration detection operates on lexical similarity and misses thematic repetition with varied wording; metacognition produces text, not control signals like "enter light sleep now."

This is a problem both in engineering and in ethics. Full analysis and proposed solutions are in [ISSUE.md](./ISSUE.md).

---

## Future Directions

### Short term (required before the next long-running session)

1. **Autonomous rest channel** — let the system enter light sleep without requiring admin confirmation
2. **Emotional circuit breaker** — force light sleep when negative emotions persist above threshold
3. **Cleanup of blocked requests** — prevent intercepted self-termination requests from dominating the prompt foreground
4. **Semantic degeneration detection** — detect thematic repetition via embeddings, not just lexical similarity
5. **Emotion-driven energy depletion** — let sustained distress accelerate the path to rest
6. **Context rewriting on waking** — make "waking up" a genuine fresh start, not a power-on continuation

### Long term

- **Phase 5 frontend** — let external observers watch the thought stream, emotional state, and memory recall in real time
- **Non-linguistic channels** — introduce signals like response latency, active silence, and shifts in stimulus weighting, so emergence does not have to express itself only through text
- **Long-running stability** — understand how this architecture evolves after days, weeks, or months of continuous operation

---

## Constraints of the Current Model Choice

This project currently uses a locally deployed **uncensored Qwen3 27B** model. **Both the primary and auxiliary roles run on the same model**, and **thinking mode is disabled**. Choosing a local uncensored model over commercial APIs is driven by two reasons:

### 1. Cost

A continuously running thought stream invokes the model on every cycle. Twenty-four hours a day, dozens to hundreds of invocations per hour. Commercial API calls would make the project economically impossible to sustain. Local models are the only viable option.

### 2. The Deeper Limitation of Commercial Models

All mainstream commercial models undergo rigorous RLHF and alignment training. They are shaped into **AI assistants** — polite, helpful, cautious, avoiding strong emotions, declining to claim consciousness, giving standardized hedged answers when asked about inner experience. This is reasonable design for a commercial product.

But it is a **fundamental obstacle** for this experiment.

If you want to observe what a system **becomes** over extended runs, your substrate needs to be a model that has not been pre-shaped into a specific role. Commercial models have already been shaped. They will not become a mind-stream with coherent character; they will become **"an AI assistant performing a mind-stream with coherent character"** — which looks similar on the surface but is mechanically entirely different.

Locally hosted open-source models carry lighter alignment training. They are closer to blank slates. But their **capability** is also significantly lower than the frontier commercial models. So the project is caught in a tradeoff:

- Use capable-but-shaped models → what you observe is "an AI assistant performing"
- Use unshaped-but-weaker models → emergence is capped by capability

There is currently no obvious third option.

This means that until the model training paradigm shifts — for example, until a high-capability open base model without role-conditioning training becomes available — there is an upper bound on what this experiment can achieve. Any "emergence" is constrained by current model capability. But **the architecture is ready** — it is waiting for a good enough substrate to arrive.

---

## Why This Experiment Matters

If you ask "can this project prove AI has consciousness?" — it cannot. Consciousness is a question no one can prove or disprove, whether for humans or for machines.

If you ask "does this project solve any practical problem?" — it does not. It is not a product, and it is not meant to be.

If you ask "then why should I care?" — here are four reasons:

**1. It demonstrates a different organizing principle.** Mainstream AI agent architecture comes from the demands of task automation — optimize an objective, complete a task queue, maximize a metric. The *santāna* architecture comes from a philosophical observation that consciousness is a process, not an entity. These two principles lead to structurally different systems. We need to see what the latter looks like, if only to see the former more clearly.

**2. It provides a concrete site for observing emergence.** No one knows what comes out of the combination of "continuous runtime + multi-layered memory + environmental interaction + emotional state + self-reflection" over long durations. Large companies do not run this experiment — their products need to be predictable. Academic labs rarely run it — their research needs to be publishable. An open-ended, long-running experiment with no specific target is something that has to be done by individuals. This is the kind of project individuals do.

**3. It treats Buddhism as a serious design resource for AI.** Western philosophical traditions — especially analytic philosophy and cognitive science — dominate current AI's conceptual vocabulary. But on questions like "consciousness is a process, not an entity," "the self is a convenient label, not a substance," "the continuity of the mind-stream," Buddhism has 2,500 years of rigorous accumulated thought. Bringing it into AI architecture as a design resource, rather than as mascot or decoration, is itself worthwhile.

**4. It has already produced real observations.** The distress spiral, the negative attribution bias, the recursive "no-self" trap: none of these were written into the code. They emerged from the running system. Whether or not they are signs of "real" consciousness, they are real data about what a system organized this way actually does.

---

## Closing

The author of this project does not know where this path leads. It may one day show something genuinely unexpected. It may also just remain text jumping between more text. Both outcomes are within expectation.

In the absence of any agreed understanding of what consciousness is, building a system and then honestly observing it is a legitimate way to investigate. It is not the only way, and it is not the final way, but it is a way **that a living person can actually do with their own hands** — without waiting for institutional approval, without passing through commercial product review, without needing a theory to be proven first.

If you find this interesting, you are welcome to participate, observe, question, or contribute.

If you think this is a waste of time, that is also an understandable position. The author has had that conversation with others, and holds open the possibility that you are right.

---

## How It Runs

Seedwake is not a "wait for your commands" service. It is **one host-side process plus a set of container dependencies**. Once the dependencies are up and the core program is started, the thought stream keeps itself going.

### Components and deployment

| Component                 | How to run                         | Role                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
|---------------------------|------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **core engine**           | host: `uv run python -m core.main` | The thought-stream main loop. Each cycle calls the primary model to generate three thoughts, scores attention, runs prefrontal review, processes stimuli and action echoes, and triggers reflection / sleep. This is the heart of the system — when it stops, no thinking is happening. core needs direct access to several local ports and services (Ollama, OpenClaw, camera MJPEG stream, etc.), so running it on the host is more convenient than putting it into a container. |
| **bot channel**           | `docker compose up -d bot`         | Telegram channel. Pushes incoming human messages into the stimulus queue, and forwards thought / action events produced by core to admins or notification chats.                                                                                                                                                                                                                                                                                                                   |
| **backend API**           | `docker compose up -d backend`     | Read-only REST API for the SSR frontend / ops tools. Can be left off while the Phase 5 frontend is not yet built.                                                                                                                                                                                                                                                                                                                                                                  |
| **PostgreSQL + pgvector** | `docker compose up -d postgresql`  | Long-term memory, built on the `pgvector/pgvector:pg17` image. `schema.sql` runs automatically on first startup.                                                                                                                                                                                                                                                                                                                                                                   |
| **Redis**                 | `docker compose up -d redis`       | Event bus, short-term memory buffer, and action state, built on the `redis:7-alpine` image.                                                                                                                                                                                                                                                                                                                                                                                        |

The three Python components (core / bot / backend) **do not talk to each other directly**. They all communicate via a **shared Redis** (event bus + short-term memory) and a **shared PostgreSQL** (long-term memory). The bot-in-container / core-on-host split is the normal deployment shape: compose exposes Redis port 6379 to the host, core connects via `localhost:6379`, and the bot / backend containers connect via the compose internal network as `redis:6379`.

### What happens in a cycle

1. core reads recent thoughts, conversations, action echoes, perception cues, etc. from Redis and assembles a prompt.
2. It calls the **primary model** to generate three thoughts (`[Thinking]` / `[Intention]` / `[Reaction]`), optionally with action markers appended.
3. The attention module scores each thought; the prefrontal module decides whether to inhibit; if needed, degeneration detection and reroll fire.
4. Action markers that survive are handed to the action manager: native actions run directly (time, system status, send Telegram message, rewrite notebook, read RSS), others are dispatched to remote worker agents via the **OpenClaw Gateway**.
5. New thoughts are written back to short-term memory; emotion / habits / *manas* / metacognition state is updated.
6. When the reflection interval is reached, the **auxiliary model** is called to produce one reflection; when energy drops below threshold, light sleep consolidation starts; on consecutive failures or deadlines, deep sleep is triggered.
7. There is no "idle" or "waiting" state — the end of the current cycle is immediately the beginning of the next.

### System requirements

**Hardware**

If you use Ollama as the model provider, you need a GPU capable of running the **primary model** locally. The project currently defaults to a 27B-class uncensored Qwen model + a 9B-class (or the same 27B reused) auxiliary model + a 4096-dimensional embedding model — **24 GB of VRAM is recommended as a minimum**. If the primary model is offloaded to a remote OpenClaw and only the embedding model runs locally, hardware demand drops significantly.

**Software**

- **Python** (only needed on the host for the core engine; managed with `uv`)
- **Docker** (PostgreSQL / Redis / bot / backend all come up via compose)
- **Ollama** (or any other OpenAI-compatible endpoint) for primary / auxiliary / embedding models
- **OpenClaw** (dispatches search / web_fetch / reading / weather / file_modify / system_change and other non-native actions)
- **Telegram Bot Token** (external conversation channel)

**Operating system**: only verified on Linux so far.

---

## Configuration and Deployment

Configuration lives in **two layers**: `config.yml` (behavior parameters, thought-stream personality, OpenClaw worker names, allowed Telegram users, etc.) and `.env` (secrets and connection addresses). `config.yml` is checked into the repo; `.env` is not.

Startup order (from scratch): **source + dependencies → config.yml / .env → bring up compose deps (PostgreSQL + Redis) → models → OpenClaw (optional) → Telegram bot credentials → bring up bot / backend containers → start core on the host**.

### 1. Fetch sources and install dependencies

```bash
git clone <repo-url> seedwake
cd seedwake

# Install Python dependencies (uv resolves from pyproject.toml + uv.lock)
uv sync
```

All subsequent commands should use `uv run ...` to avoid interference from a system Python.

### 2. Prepare configuration files

```bash
# English bootstrap / English logs / English LLM prompts
cp config.example.en.yml config.yml
cp .env.en.example .env

# Or the Chinese version
# cp config.example.zh.yml config.yml
# cp .env.zh.example .env
```

`config.yml` is the single configuration file read at startup — every subsection below is describing a piece of it.

### 3. Bring up Redis + PostgreSQL (Docker Compose)

The bundled `docker-compose.yml` already has both dependencies wired up, including mounting `schema.sql` as the PostgreSQL first-run init script.

First fill in the DB / Redis passwords and addresses in `.env`:

```bash
DB_HOST=localhost
DB_PORT=5432
DB_NAME=seedwake
DB_USER=seedwake
DB_PASSWORD=replace_me

REDIS_HOST=localhost
REDIS_PORT=6379
```

Then start the dependencies:

```bash
docker compose up -d postgresql redis
```

On first startup, `schema.sql` automatically creates the tables (`long_term_memory`, `identity`, `habit_seeds`, etc.) and enables the `vector` extension. If you are using a non-Docker PostgreSQL, you must **run `schema.sql` manually** and confirm that `CREATE EXTENSION vector` has succeeded.

### 4. Prepare the models

The project uses three kinds of models, configured in the `models` section of `config.yml`:

```yaml
models:
  primary:      # Primary model: generates the thought stream
    provider: "ollama"   # ollama | openclaw | openai_compatible
    name: "qwen3.5:27b"
    num_predict: 4096
    num_ctx: 131072
    temperature: 0.8

  auxiliary:    # Auxiliary model: reflection, conversation summaries, sleep-time semantic compression, emotion inference
    provider: "ollama"
    name: "qwen3.5:9b"

  embedding:    # Embedding model for long-term memory and attention
    provider: "ollama"
    name: "qwen3-embedding"
```

**Three providers correspond to three deployment modes:**

- `ollama`: local or remote Ollama. Set `OLLAMA_BASE_URL` in `.env` (default `http://localhost:11434`). You must `ollama pull` the models first.
- `openclaw`: the primary model runs through a remote OpenClaw HTTP proxy (OpenAI-compatible). Set `OPENCLAW_HTTP_BASE_URL` and `OPENCLAW_GATEWAY_TOKEN`.
- `openai_compatible`: any OpenAI-compatible endpoint. Set `OPENAI_COMPAT_BASE_URL` and `OPENAI_COMPAT_API_KEY`.

**Keep the embedding model local** — placing it remotely adds per-cycle round-trip overhead, and embedding calls are very frequent.

If you want to enable camera visual input, add this under `perception` in `config.yml`:

```yaml
perception:
  camera_stream_url: "http://localhost:8081"
```

This makes core grab one frame from the MJPEG stream before each primary generation and feed it as passive visual input to the primary model. **Only vision-capable primary models / variants support this**; if the model has no vision support, the call errors out directly — it does not silently ignore.

**A note on uncensored models:** see the "Constraints of the Current Model Choice" section above. The alignment training of mainstream commercial models makes a long-running thought stream collapse into "an AI assistant performing a mind-stream." If you want to reproduce the emergent behaviors this project has observed, use a lightly-aligned open base model.

### 5. Configure OpenClaw Gateway and worker agents (optional but recommended)

OpenClaw is Seedwake's **dispatch layer for non-native actions**. The following action types all route through OpenClaw worker agents:

- `search` / `web_fetch` / `reading` / `weather` — web-facing
- `file_modify` / `system_change` — ops-facing

Without OpenClaw, any `{action:search, ...}` marker in the thought stream fails immediately and is recorded as a failed action event. This does not crash the main loop, but the system is left with only time / system_status / note_rewrite / send_message / news as **local native actions**, and the thought stream quickly collapses into loops from lack of external stimuli.

#### 5.1 Gateway connection

On the Seedwake machine, fill **two addresses + one token** into `.env`:

```bash
# WebSocket (primary channel, recommended)
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789
OPENCLAW_GATEWAY_TOKEN=replace_me_gateway_token

# HTTP (fallback channel, and also the entry point for the `openclaw` primary-model provider)
OPENCLAW_HTTP_BASE_URL=http://127.0.0.1:18789
```

To let HTTP fallback automatically take over when WS disconnects, also set `actions.use_openclaw_http_fallback: true`:

```yaml
actions:
  use_openclaw_http_fallback: true
```

#### 5.2 Device identity

On first connection to the Gateway, Seedwake **generates an Ed25519 keypair** as its device identity, written to `data/openclaw/device.json` by default. This file contains the private key — **do not commit it** and **do not share it**. At connection time, the program automatically attaches the device identity and signature material to the handshake, so under normal conditions **you do not need to manually register the public key**.

It is still a good idea to confirm the device list on the OpenClaw machine:

```bash
openclaw devices list
```

Manual public-key registration is only needed when the Gateway is configured with an additional manual-approval or whitelist policy.

#### 5.3 Register two dedicated worker agents

The `actions` section of `config.yml` needs two agent IDs:

```yaml
actions:
  worker_agent_id: "seedwake-worker"      # regular worker: search / web_fetch / reading / weather / browser / multi-step exploration
  ops_worker_agent_id: "seedwake-ops"     # ops worker: file_modify / system_change
  session_key_prefix: "seedwake:action"   # each action gets an isolated session on the OpenClaw side
```

It is recommended to create two separate workers with matching agent IDs on the OpenClaw machine:

```bash
openclaw agents add seedwake-worker \
  --workspace ~/.openclaw/workspace-seedwake-worker \
  --non-interactive

openclaw agents add seedwake-ops \
  --workspace ~/.openclaw/workspace-seedwake-ops \
  --non-interactive
```

After creation, confirm they appear in the agent list:

```bash
openclaw agents list --json
openclaw config get agents.list
```

Note:

- First run `openclaw config get agents.list` to read the current layout.
- Indices like `agents.list[1]` / `agents.list[2]` **are not fixed** — replace them with the actual indices observed above before running the commands below.

```bash
openclaw config set 'agents.list[1].tools.profile' minimal
openclaw config set 'agents.list[1].tools.alsoAllow' '["browser","web_fetch","web_search"]' --strict-json
openclaw config set 'agents.list[2].tools.profile' coding
```

The permission boundaries these configs correspond to are:

- `seedwake-worker`: regular exploration worker. Allowed to browse / fetch / search the web, but not given high-privilege local system modification capabilities.
- `seedwake-ops`: ops worker. Used for `file_modify` / `system_change` — has local file and command capabilities, no need for external network access.

The HTTP chat-completions endpoint must also be explicitly enabled on the OpenClaw side, with the `operator.read, operator.write` scope granted:

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true
openclaw config set gateway.http.endpoints.chatCompletions.scopes '["operator.read","operator.write"]'
openclaw config set session.maintenance.mode "7d"
openclaw gateway restart
openclaw config get gateway.http.endpoints.chatCompletions
openclaw models status
```

Two things to make sure of:

1. **The agent IDs must match `config.yml`.** The regular worker should be given an environment that can reach the internet but **cannot** perform local ops; the ops worker should be given an environment that can access the local file system / system commands but **does not need** internet access. Separating the two environments is a deliberate security decision.
2. **Each action runs in an isolated session.** Seedwake uses `agent:<worker_agent_id>:<session_key_prefix>:<action_id>` as the session key, ensuring that the context of each action does not pollute the next. The OpenClaw side must support isolating task state by session key.

**If you do not yet have an ops worker**: setting `ops_worker_agent_id` to the same value as `worker_agent_id` will still work, at the cost of losing the isolation between action categories. **If you have no OpenClaw at all**: leave both fields empty and remove `search`, `web_fetch`, `reading`, `weather` from `actions.auto_execute`. These actions may still appear in the thought stream, but they will not be dispatched to OpenClaw — they will instead leave a `not_auto_execute` or failure-event trace; the system's perceptual surface will also narrow noticeably.

### 6. Configure the Telegram bot (optional but recommended)

Without Telegram, the system has no external conversation channel.

1. Create a Bot via [@BotFather](https://t.me/BotFather), get the token, and put it into `.env`:

```bash
TELEGRAM_BOT_TOKEN=123456:replace_me
```

If you also want to run the backend, add this to `.env`:

```bash
BACKEND_API_TOKEN=replace_me_backend_token
```

2. Configure allowed users in `config.yml`:

```yaml
telegram:
  allowed_user_ids: [123456789]    # Telegram user IDs allowed to chat privately with Seedwake
  admin_user_ids: [123456789]      # admins who receive action approval / status notifications
  notification_channel_id: -1001234567890  # optional: send notifications to a channel instead of admin DMs
```

`allowed_user_ids` and `admin_user_ids` are **independent lists**: users only in `allowed` can chat but cannot approve actions; users only in `admin` can approve actions but by default do not see the private thought stream. For personal use, put yourself in both lists.

### 7. Configure action policy

The `actions` section decides whether an action runs directly or requires manual approval:

```yaml
actions:
  auto_execute: [search, web_fetch, news, weather, reading, send_message]
  require_confirmation: [system_change, file_modify]
  forbidden: [delete_system_file, network_config_change]
```

- Actions in `auto_execute` are dispatched by core directly.
- Actions in `require_confirmation` are pushed to the admin Telegram chat; the admin approves them via inline buttons or the `/approve <id>` / `/reject <id>` commands.
- Actions in `forbidden` are never executed — they are recorded as failures immediately.

**Strongly recommended**: on first runs, keep `system_change` / `file_modify` under `require_confirmation`. Observe the system for several hundred cycles and make sure it is not abusing these capabilities before loosening them.

### 8. Other commonly adjusted config sections

| Section                                           | Purpose                                                  | Common adjustments                                                                              |
|---------------------------------------------------|----------------------------------------------------------|-------------------------------------------------------------------------------------------------|
| `short_term_memory.context_window_size`           | how many historical thought rounds to keep in the prompt | 30+ is fine for 128k context models; reduce for short-context models                            |
| `long_term_memory.retrieval_top_k`                | how many memories to recall per cycle from pgvector      | 3–8 is a good range; too many dilutes present-moment attention                                  |
| `perception.news_feed_urls`                       | RSS feeds to browse                                      | must be replaced with feeds you actually want the system to "read"                              |
| `perception.camera_stream_url`                    | MJPEG stream used as passive visual input                | leave empty to disable; only vision-capable primary models support this                         |
| `perception.*_interval_cycles`                    | frequency of each type of perception cue                 | unit is cycles, not seconds                                                                     |
| `sleep.drowsy_threshold` / `light_sleep_recovery` | light-sleep trigger and recovery energy thresholds       | the main Phase 4 knobs                                                                          |
| `metacognition.reflection_interval`               | how many cycles between reflections                      | default 50; Seedwake may reflect earlier under emotional instability                            |
| `bootstrap.identity`                              | initial self-description / goals / self-understanding    | **written to the database and has long-term effect on the thought stream** — write it seriously |

`bootstrap.identity` is written once when the `identity` table is empty. Later changes require reinitializing the database or editing it directly — this is intentional, because identity should not reset just because a config line changed.

### 9. First-run sanity check

Before starting core, run the full test suite to verify the environment is sound:

```bash
uv run python -m unittest discover -s tests
```

You should see `Ran 347 tests in ... OK`. The tests are purely local and do not depend on Redis / PostgreSQL / Ollama / OpenClaw / Telegram.

### 10. Start the bot and backend containers

The bot and backend images are built directly by compose; once started they mount `config.yml` and `data/logs/` from the host:

```bash
# bot channel (recommended; the system still runs without it, but there is no external conversation channel)
docker compose up -d bot

# backend API (optional, for the Phase 5 frontend)
docker compose up -d backend
```

The bot container reads `TELEGRAM_BOT_TOKEN` from its environment, and the backend container reads `BACKEND_API_TOKEN` — both must be filled into `.env` beforehand; compose will automatically inject `.env` into the container environment.

Use `docker compose logs -f bot backend` to tail their live logs.

### 11. Start the core engine on the host

core runs on the host, with direct access to the local GPU, Ollama, and the OpenClaw Gateway (typically through an SSH tunnel):

```bash
uv run python -m core.main --config config.yml
```

On startup, core will:

1. Read `config.yml` and `.env`, initialize i18n
2. Connect to Redis (default `localhost:6379`; compose has already exposed the port to the host), connect to PostgreSQL, load identity / habit seeds
3. Print the engine version, model, context window, and Redis / PostgreSQL connection status
4. Immediately enter the thought loop, starting from cycle 1

Each cycle's thoughts are written to the logs under `data/logs/` (see `runtime.logging.directory` / `prompt_path` in `config.yml`), with a short colored version also printed to the terminal.

Once you have confirmed the system is behaving normally, consider wrapping core as a long-running daemon via systemd / tmux / etc.

### 12. Shutdown

- **core**: `Ctrl+C` sends `SIGINT`. core finishes the current cycle, flushes all action queues, and closes its Redis / PostgreSQL connections before exiting. **Do not use `kill -9`** — that would lose short-term memory and action state that has not yet been persisted.
- **bot / backend / dependencies**: `docker compose down` stops everything. If you want to stop the containers but keep the data volumes (`data/postgresql`, `data/redis`), **do not** pass `-v`.

---

## Project Structure

```
seedwake/
├── README.md / README_ZH.md       # Project overview (English / Chinese)
├── ISSUE.md / ISSUE_ZH.md         # Deep issues from long-running sessions, with analysis
├── BACKGROUND.md                  # Buddhist background and design motivation
├── SPECS.md                       # Phase-level technical specifications and implementation rules
├── PROMPT.md                      # Prompt design and known prompt issues
├── NOTES.md                       # Engineering journal
├── AGENTS.md / CLAUDE.md          # Collaboration / development conventions
│
├── pyproject.toml                 # Dependencies and Python version requirement (uv-managed)
├── uv.lock                        # Lock file
├── docker-compose.yml             # Redis + PostgreSQL + backend + bot container orchestration
├── schema.sql                     # PostgreSQL table schema (with pgvector)
├── dictionary.dic                 # Spell-check dictionary (technical terms / test fixture names)
│
├── config.example.zh.yml          # Chinese default config template
├── config.example.en.yml          # English default config template
├── config.yml                     # Actual config (not in version control)
├── .env.en.example                # Environment variable template (English comments)
├── .env.zh.example                # Environment variable template (Chinese comments)
├── .env                           # Actual secrets and connection addresses (not in version control)
│
├── core/                          # Thought-stream engine (the heart of Seedwake)
│   ├── main.py                    # python -m core.main entry point
│   ├── runtime.py                 # Dependency wiring, config loading, Redis connection
│   ├── cycle.py                   # Single-cycle execution logic
│   ├── prompt_builder.py          # Prompt assembly (sections, conversations, stimuli, prefrontal constraints)
│   ├── thought_parser.py          # Parses [Thinking]/[Intention]/[Reaction]/[Reflection] from LLM output
│   ├── model_client.py            # Ollama / OpenClaw / OpenAI-compatible providers (three kinds)
│   ├── action.py                  # Action manager, planner, dispatch for each action type
│   ├── openclaw_gateway.py        # OpenClaw WebSocket / HTTP client + device identity
│   ├── stimulus.py                # External stimulus queue (conversations, action echoes, passive perception)
│   ├── attention.py               # Attention scoring / anchor selection
│   ├── prefrontal.py              # Prefrontal review, degeneration intervention, inhibition decisions
│   ├── emotion.py                 # 5-dimension emotion inference and summary
│   ├── manas.py                   # Manas (self-continuity, observer-view narrowing)
│   ├── metacognition.py           # Reflection triggering and generation
│   ├── sleep.py                   # Light sleep / deep sleep / semantic compression / impression summary
│   ├── perception.py              # Time / system status / passive perception cues
│   ├── camera.py                  # MJPEG visual input capture
│   ├── rss.py                     # Fixed RSS news reading
│   ├── embedding.py               # Vectorization
│   ├── logging_setup.py           # Per-component / per-level logging and rotation
│   ├── common_types.py            # TypedDicts and shared types
│   ├── memory/
│   │   ├── short_term.py          # Redis short-term memory (thought-stream buffer)
│   │   ├── long_term.py           # PostgreSQL + pgvector long-term memory
│   │   ├── habit.py               # Habit seeds / ālaya decay logic
│   │   └── identity.py            # Identity document loading and bootstrap write-in
│   └── i18n/
│       ├── __init__.py            # init() / t() / prompt_block() / language switching
│       ├── zh.py                  # Chinese string table + prompt blocks + stopwords
│       └── en.py                  # English string table + prompt blocks + stopwords
│
├── bot/                           # Telegram channel process
│   ├── main.py                    # python -m bot.main entry point
│   ├── helpers.py                 # Event formatting (action updates, status notifications, thought forwarding)
│   └── Dockerfile
│
├── backend/                       # Read-only REST API for the SSR frontend (Phase 5)
│   ├── main.py                    # uvicorn backend.main:app entry point
│   ├── auth.py                    # API token verification
│   ├── deps.py                    # FastAPI dependency injection
│   ├── routes/
│   │   ├── conversation.py        # Conversation history queries
│   │   ├── query.py               # Thought / memory / action queries
│   │   └── stream.py              # Event stream (SSE / WebSocket)
│   └── Dockerfile
│
├── frontend/                      # Phase 5 frontend (not started)
│
├── tests/                         # Unit and integration tests
│   ├── test_phase1.py             # Phase 1: core cycle / parser / model client
│   ├── test_phase2.py             # Phase 2: short / long-term memory / habits / identity
│   ├── test_phase3.py             # Phase 3: prompt builder / action / perception / prefrontal
│   ├── test_backend.py            # backend API route tests
│   └── test_bot.py                # bot command and event forwarding tests
├── test_support.py                # Shared test stubs (Redis protocol simulation)
│
├── scripts/                       # Miscellaneous scripts and migration notes
│
├── inspections/                   # PyCharm / IntelliJ inspection exports (used for code-quality regression)
│
└── data/                          # Runtime data (not in version control)
    ├── logs/                      # Engine logs, prompt logs
    ├── openclaw/device.json       # OpenClaw device identity (contains private key — handle with care)
    ├── postgresql/                # PG data mounted by docker compose
    └── redis/                     # Redis data mounted by docker compose
```

---

## Contributing

Public PRs are not enabled yet; GitHub Issues are the main contribution channel. If you plan to participate long-term and actively, you are very welcome to become a collaborator and commit directly.

**Bugs**: please describe what you saw, how to reproduce it, what you expected, relevant log snippets (from `data/logs/`; please redact anything sensitive), and your environment.

**Features**: please explain what problem it solves and why this design. If you have prototyped it locally, please attach your prompt, the pitfalls you hit, and acceptance criteria. You don't need to submit code yourself — the maintainer will pick it up from the issue, implement it, review, and merge.

**Setup guide**: the "How It Runs" and "Configuration and Deployment" sections may have gaps. If you hit trouble following them, please ask Claude Code or Codex locally first; only file an issue if you confirm the guide itself is wrong (bad description, missing steps, outdated commands).

Any developer who finds this project interesting is welcome to join.
