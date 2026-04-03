"""Attention evaluation for Phase 4.

Uses goal relevance, emotion resonance, novelty, and external stimulus priority.
Avoids brittle keyword rules by relying on text similarity plus structural cues.
"""

from dataclasses import dataclass

from core.stimulus import Stimulus
from core.thought_parser import Thought, strip_action_markers, thought_action_requests
from core.common_types import AttentionPromptEntry, EmotionSnapshot, HabitPromptEntry, bigram_similarity

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
    *,
    goal_stack: list[str] | None = None,
    emotion: EmotionSnapshot | None = None,
    active_habits: list[HabitPromptEntry] | None = None,
) -> AttentionResult:
    recent_texts = [
        thought.content
        for thought in recent_thoughts[-NOVELTY_WINDOW:]
        if thought.content.strip()
    ]
    entries: list[tuple[float, str, Thought]] = []
    for thought in thoughts:
        score, reason = _attention_score(
            thought,
            recent_texts,
            stimuli,
            goal_stack or [],
            emotion,
            active_habits or [],
        )
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
    stimuli: list[Stimulus],
    goal_stack: list[str],
    emotion: EmotionSnapshot | None,
    active_habits: list[HabitPromptEntry],
) -> tuple[float, str]:
    score = 0.15
    reasons: list[str] = []

    goal_relevance = _maxbigram_similarity(thought.content, goal_stack)
    score += goal_relevance * 0.28
    if goal_relevance >= 0.16:
        reasons.append("贴近目标")

    # Novelty: how different is this thought from recent ones (bigram Jaccard)
    novelty = _novelty_score(thought.content, recent_texts)
    score += novelty * 0.35
    if novelty >= 0.4:
        reasons.append("较新")

    emotion_resonance = _emotion_resonance_score(thought, stimuli, emotion)
    score += emotion_resonance * 0.18
    if emotion_resonance >= 0.2:
        reasons.append("契合情绪")

    habit_resonance = _habit_resonance_score(thought, active_habits)
    score += habit_resonance * 0.14
    if habit_resonance >= 0.2:
        reasons.append("触发现行习气")

    # Structural bonuses
    if thought.trigger_ref:
        score += 0.12
        reasons.append("有触发源")
    stimulus_bonus, stimulus_reason = _external_priority_bonus(thought, stimuli)
    score += stimulus_bonus
    if stimulus_reason:
        reasons.append(stimulus_reason)
    if thought_action_requests(thought):
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
    similarities = [bigram_similarity(normalized, _normalize_text(text)) for text in recent_texts]
    highest_similarity = max(similarities) if similarities else 0.0
    return max(0.0, 1.0 - highest_similarity)


def _maxbigram_similarity(content: str, targets: list[str]) -> float:
    normalized = _normalize_text(content)
    if not normalized or not targets:
        return 0.0
    similarities = [
        bigram_similarity(normalized, _normalize_text(target))
        for target in targets
        if _normalize_text(target)
    ]
    return max(similarities) if similarities else 0.0


def _emotion_resonance_score(
    thought: Thought,
    stimuli: list[Stimulus],
    emotion: EmotionSnapshot | None,
) -> float:
    if emotion is None:
        return 0.0
    dimensions = emotion["dimensions"]
    summary_similarity = bigram_similarity(
        _normalize_text(thought.content),
        _normalize_text(emotion["summary"]),
    )
    score = summary_similarity * 0.25
    curiosity = float(dimensions.get("curiosity", 0.0))
    concern = float(dimensions.get("concern", 0.0))
    frustration = float(dimensions.get("frustration", 0.0))
    calm = float(dimensions.get("calm", 0.0))
    action_types = {
        str(request.get("type") or "").strip()
        for request in thought_action_requests(thought)
    }
    if thought.type == "思考" or action_types & {"reading", "search", "web_fetch", "news"}:
        score += curiosity * 0.35
    if any(stimulus.type == "conversation" for stimulus in stimuli) and thought.type in {"反应", "意图"}:
        score += concern * 0.35
    if thought.type == "反思":
        score += max(frustration, calm) * 0.18
    return min(1.0, score)


def _external_priority_bonus(thought: Thought, stimuli: list[Stimulus]) -> tuple[float, str]:
    if not stimuli:
        return 0.0, ""
    priorities = sorted(stimuli, key=lambda stimulus: stimulus.priority)
    highest = priorities[0]
    if highest.type == "conversation" and thought.type == "反应":
        return 0.20, "承接对话"
    if highest.type == "action_result" and thought.type in {"反应", "意图"}:
        return 0.14, "承接回音"
    if thought.type == "反应":
        return 0.10, "承接外界刺激"
    return 0.0, ""


def _habit_resonance_score(
    thought: Thought,
    active_habits: list[HabitPromptEntry],
) -> float:
    manifested = [habit for habit in active_habits if habit.get("manifested")]
    if not manifested:
        return 0.0
    thought_text = _normalize_text(strip_action_markers(thought.content))
    if not thought_text:
        return 0.0
    similarities = [
        bigram_similarity(
            thought_text,
            _normalize_text(str(habit["pattern"])),
        )
        * max(0.3, float(habit.get("activation_score") or 0.0))
        for habit in manifested
        if _normalize_text(str(habit["pattern"]))
    ]
    return min(1.0, max(similarities) if similarities else 0.0)


def _normalize_text(text: str) -> str:
    return " ".join(str(text).replace("\n", " ").split())
