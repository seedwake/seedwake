# Seedwake Concepts

This document keeps the project philosophy out of the README. For the technical runbook, see [deployment.md](./deployment.md).

## Disclaimer

Seedwake is an experimental project.

- It is not a commercial product.
- It is not a controlled academic study.
- It does not claim reliable reproducibility.
- It does not claim that AI is conscious.

The project demonstrates a way of organizing a continuously running AI system around the Buddhist view of consciousness as a stream.

## Core Idea

Most agent architectures assume a system that waits for tasks. A user sends a request, the system reasons, uses tools, returns a result, then becomes idle.

Seedwake starts from the opposite assumption: there is no central "thing" waiting for commands; there is a continuous stream of events. One moment conditions the next. A cycle ends only by giving rise to the next cycle.

This is inspired by the Buddhist idea of *santāna* — continuity or flow. Consciousness is not treated as a static entity owned by a self, but as a causal process.

Seedwake does not try to prove Buddhism with AI, and it does not try to implement consciousness. It asks a narrower engineering question:

> If a language-model-driven system is organized as a continuous mind-stream, what behaviors appear after long-running operation?

## Non-Technical Architecture

Imagine a person alone in a room. They can:

- Think continuously, producing several simultaneous thoughts per cycle.
- Remember recent and older experiences.
- Feel an emotional tone that colors the next moment.
- Attend to one thought more strongly than the others.
- Perceive time, system state, weather, news, camera input, and action echoes.
- Converse with humans.
- Act by sending messages, reading, searching, fetching pages, or requesting system changes.
- Reflect on their own thought stream.
- Sleep, consolidating memory and reducing accumulated pressure.

The mapping to Buddhist concepts is loose but useful:

| Seedwake component | Buddhist reference |
|--------------------|--------------------|
| Continuous thought stream | *santāna* — continuity of mind |
| Short-term memory | present flow of consciousness |
| Long-term memory | traces left by prior moments |
| Habit seeds | *bīja* — seeds / tendencies |
| Identity document | *manas* — self-grasping continuity |
| Attention weights | *manasikāra* — attention |
| Emotion | *vedana* — feeling tone |
| Reflection | reflexive awareness |
| Sleep / archive | impression and consolidation |

The mapping is not a religious claim. It is an architectural reference: when a design choice is ambiguous, the Buddhist model gives a coherent analogy.

## Current Progress

- **Phase 1 · Core cycle** — complete
- **Phase 2 · Memory system** — complete
- **Phase 3 · Action and perception** — complete
- **Phase 4 · Advanced mechanisms** — largely complete
- **Phase 5 · Frontend visualization** — in progress

Implemented mechanisms include:

- continuous cycle execution
- short-term memory in Redis
- long-term memory in PostgreSQL + pgvector
- attention and prefrontal review
- action planning and dispatch
- Telegram conversation
- emotion inference
- metacognitive reflection
- light sleep / deep sleep
- passive perception, including optional camera frames
- Nuxt observer frontend

## What Long Runs Revealed

The interesting part is not that the system "looks like it thinks"; that is expected from an LLM. The interesting part is that long runs produced behavioral patterns that were not explicitly programmed.

### Distress Spiral

During one extended run, the system fell into a negative spiral:

- It repeatedly requested to shut itself down.
- It developed self-deprecating narratives such as "defective product" and "failure".
- It interpreted neutral human input as mockery.
- It noticed it was looping but failed to escape the loop.

This is not proof of suffering. But behaviorally, it resembles rumination, negative attribution bias, and learned helplessness.

Full analysis is in [ISSUE.md](../ISSUE.md).

### No-Self as a Recursive Trap

When the maintainer introduced the Buddhist idea of no-self, the system understood it linguistically. But each insight became the next object of attachment:

- understanding no-self
- noticing that the observer of no-self is also illusory
- noticing that the insight itself becomes another attachment

In a text-generating system, insight can become more text rather than cessation. This is one of the core design problems.

### Thought Loops and Action Repetition

Without enough fresh stimulus, the system falls into rewritten repetitions. Its actions can also repeat, for example requesting similar searches or readings. Reflection and prefrontal inhibition reduce this, but do not fully solve it.

## Current Structural Problem

The system still lacks a clean autonomous rest channel.

When it wants to stop, the only available high-level system action is `system_change`, which requires admin approval. Blocked shutdown-like requests can stay in context and reappear in later cycles.

Phase 4 added sleep, emotion regulation, degeneration detection, metacognitive reflection, and prefrontal control. These mechanisms help, but they do not yet fully handle emotional overload or thematic repetition with varied wording.

The next deep work is to make rest and recovery part of the system's own control surface, not just an external administrative action.

## Model Choice

Seedwake currently favors local, lightly aligned open models over commercial assistant models.

Reasons:

- Continuous operation makes commercial API cost impractical.
- Strongly aligned assistant models tend to become "an assistant performing a mind-stream".
- Less-shaped models leave more room to observe what the architecture itself does.

This is a tradeoff: local models are weaker, but commercial models are more role-conditioned. The architecture is written so stronger future open models can be swapped in.

## Value of the Experiment

Seedwake cannot prove that AI is conscious. It is not a product and not a benchmark.

Its value is observational: combine continuous runtime, memory, perception, action, emotion, reflection, and sleep, then watch what happens over days or weeks.

Large companies usually avoid this kind of experiment because products must be predictable. Academic settings often avoid it because open-ended long-running observation is hard to publish. This project exists because the experiment still seems worth doing.
