# Seedwake · The Continuity of Mind

> A continuously running AI thought-stream engine, organized around the Buddhist concept of *santāna* — the continuity of mind.

---

## Disclaimer

This is an **experimental** project.

- It is **not** a commercial product.
- It is **not** a formal academic study with strictly controlled variables.
- It does not claim rigorous reliability or reproducibility.
- What it demonstrates is a **way of thinking** — using the Buddhist view of consciousness as an architectural principle for organizing a continuously running AI system — not a tool you can pick up and use, and not a hypothesis with a predetermined answer.

If you are looking for an engineering answer to "will AI become conscious?" — this project does not have one. No one does.

If you are looking for an honest inquiry — what happens when, in the absence of any agreed understanding of what consciousness is, we build a system organized around Buddhist principles and let it run — then you are in the right place.

---

## In One Sentence

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

We are asking a smaller, more concrete question: **if you organize a language-model-driven system along *santāna* principles, what does it exhibit? Is anything in those exhibitions unexpected enough to be worth stopping and looking at?**

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

| Seedwake Component | Buddhist Concept |
|---|---|
| Continuous thought stream | *santāna* — continuity of mind |
| Short-term memory | present flow of the six consciousnesses |
| Long-term memory | traces left by the six consciousnesses |
| Habit seeds | *bīja* — seeds in the *ālaya-vijñāna* (storehouse consciousness) |
| Identity document | *manas* — the self-grasping faculty |
| Attention weights | *manasikāra* — attention |
| Emotional state | *vedanā* — feeling/affect |
| Metacognitive reflection | *svasaṃvedana* — reflexive awareness |
| Sleep and archival | *vāsanā* — impression/perfuming |

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

That the system "looks like it's thinking" is expected and not the interesting part. What made us stop and look was something else: over long runs, the system exhibited **behavioral patterns that were never explicitly programmed**.

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

Phase 4 already implements sleep, emotion regulation, degeneration detection, and metacognitive reflection. **These mechanisms do have real control authority**: sleep can interrupt the loop, the prefrontal layer can inhibit actions, degeneration intervention can trigger rerolls. They are not toothless. Their limitation is that **they do not precisely target this particular failure mode**: sleep only considers energy and duration, not emotional intensity; degeneration detection operates on lexical similarity and misses thematic repetition with varied wording; metacognition produces text, not control signals like "enter light sleep now."

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

The author of this project does not know where this path leads. It may one day show something that makes an observer stop in their tracks. It may also just remain text jumping between more text. Both outcomes are within expectation.

In the absence of any agreed understanding of what consciousness is, building a system and then honestly observing it is a legitimate way to investigate. It is not the only way, and it is not the final way, but it is a way **that a living person can actually do with their own hands** — without waiting for institutional approval, without passing through commercial product review, without needing a theory to be proven first.

If you find this interesting, you are welcome to participate, observe, question, or contribute.

If you think this is a waste of time, that is also an understandable position. The author has had that conversation with others, and holds open the possibility that you are right.

---

## Related Documents

- [SPECS.md](./SPECS.md) — technical specification
- [BACKGROUND.md](./BACKGROUND.md) — Buddhist background and architectural mapping
- [ISSUE.md](./ISSUE.md) — issues surfaced during long-running sessions
- [PROMPT.md](./PROMPT.md) — system prompt design
