"""Structural attention evaluation for Phase 4.

Uses only structural signals (action presence, trigger refs, conversation foreground,
novelty via bigram similarity) — no keyword matching for semantic judgments.
"""

from dataclasses import dataclass

from core.stimulus import Stimulus
from core.thought_parser import Thought
from core.types import AttentionPromptEntry

NOVELTY_WINDOW = 12


@dataclass(frozen=True)
class AttentionResult:
    thoughts: list[Thought]
    prompt_entries: list[AttentionPromptEntry]
    anchor_thought_id: str
    anchor_content: str


def evaluate_attention(
    thoughts: list[Thought],
    recent_thoughts: list[Thought],
    stimuli: list[Stimulus],
) -> AttentionResult:
    recent_texts = [
        thought.content
        for thought in recent_thoughts[-NOVELTY_WINDOW:]
        if thought.content.strip()
    ]
    foreground_types = {stimulus.type for stimulus in stimuli}
    entries: list[tuple[float, str, Thought]] = []
    for thought in thoughts:
        score, reason = _attention_score(thought, recent_texts, foreground_types)
        thought.attention_weight = score
        entries.append((score, reason, thought))
    ranked = sorted(entries, key=lambda item: item[0], reverse=True)
    anchor = ranked[0][2] if ranked else thoughts[0]
    prompt_entries = [
        {
            "thought_id": thought.thought_id,
            "weight": score,
            "reason": reason,
            "content": thought.content,
        }
        for score, reason, thought in ranked
    ]
    return AttentionResult(
        thoughts=thoughts,
        prompt_entries=prompt_entries,
        anchor_thought_id=anchor.thought_id,
        anchor_content=anchor.content,
    )


def select_attention_anchor(thoughts: list[Thought]) -> Thought | None:
    if not thoughts:
        return None
    ranked = sorted(
        thoughts,
        key=lambda thought: (thought.attention_weight, thought.timestamp),
        reverse=True,
    )
    return ranked[0]


def _attention_score(
    thought: Thought,
    recent_texts: list[str],
    foreground_types: set[str],
) -> tuple[float, str]:
    score = 0.15
    reasons: list[str] = []

    # Novelty: how different is this thought from recent ones (bigram Jaccard)
    novelty = _novelty_score(thought.content, recent_texts)
    score += novelty * 0.35
    if novelty >= 0.4:
        reasons.append("较新")

    # Structural bonuses
    if thought.trigger_ref:
        score += 0.12
        reasons.append("有触发源")
    if thought.type == "反应" and "conversation" in foreground_types:
        score += 0.20
        reasons.append("承接对话")
    if thought.action_request is not None:
        score += 0.12
        reasons.append("带行动冲动")
    if thought.type == "反思":
        score += 0.08
        reasons.append("元认知")

    return min(1.0, round(score, 4)), "、".join(reasons) or "自然浮现"


def _novelty_score(content: str, recent_texts: list[str]) -> float:
    normalized = _normalize_text(content)
    if not normalized or not recent_texts:
        return 0.5
    similarities = [_text_similarity(normalized, _normalize_text(text)) for text in recent_texts]
    highest_similarity = max(similarities) if similarities else 0.0
    return max(0.0, 1.0 - highest_similarity)


def _normalize_text(text: str) -> str:
    return " ".join(str(text).replace("\n", " ").split())


def _text_similarity(a: str, b: str) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    grams_a = {a[index:index + 2] for index in range(len(a) - 1)}
    grams_b = {b[index:index + 2] for index in range(len(b) - 1)}
    union = len(grams_a | grams_b)
    if union == 0:
        return 0.0
    return len(grams_a & grams_b) / union
