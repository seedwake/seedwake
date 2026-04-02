"""Rule-based attention evaluation for Phase 4."""

from dataclasses import dataclass

from core.stimulus import Stimulus
from core.thought_parser import Thought
from core.types import AttentionPromptEntry, EmotionSnapshot, HabitPromptEntry

GOAL_TOKEN_PATTERN = ("想", "目标", "继续", "回应", "完成", "保持", "避免")
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
    emotion: EmotionSnapshot,
    goal_stack: list[str],
    note_text: str,
    active_habits: list[HabitPromptEntry],
) -> AttentionResult:
    recent_texts = [
        thought.content
        for thought in recent_thoughts[-NOVELTY_WINDOW:]
        if thought.content.strip()
    ]
    foreground_types = {stimulus.type for stimulus in stimuli}
    entries: list[tuple[float, str, Thought]] = []
    for thought in thoughts:
        score, reason = _attention_score(
            thought,
            recent_texts,
            foreground_types,
            emotion,
            goal_stack,
            note_text,
            active_habits,
        )
        thought.attention_weight = score
        entries.append((score, reason, thought))
    ranked = sorted(entries, key=lambda item: item[0], reverse=True)
    anchor = ranked[0][2]
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
    emotion: EmotionSnapshot,
    goal_stack: list[str],
    note_text: str,
    active_habits: list[HabitPromptEntry],
) -> tuple[float, str]:
    score = 0.15
    reasons: list[str] = []

    novelty = _novelty_score(thought.content, recent_texts)
    score += novelty * 0.30
    if novelty >= 0.4:
        reasons.append("较新")

    goal_score = _goal_relevance_score(thought.content, goal_stack, note_text)
    score += goal_score * 0.30
    if goal_score >= 0.4:
        reasons.append("贴近目标")

    emotion_score = _emotion_resonance_score(thought.content, emotion)
    score += emotion_score * 0.20
    if emotion_score >= 0.3:
        reasons.append("贴近情绪")

    if thought.trigger_ref:
        score += 0.12
        reasons.append("有触发源")
    if thought.type == "反应" and "conversation" in foreground_types:
        score += 0.18
        reasons.append("承接对话")
    if thought.action_request is not None:
        score += 0.10
        reasons.append("带行动冲动")
    if _habit_resonance_score(thought.content, active_habits) >= 0.4:
        score += 0.10
        reasons.append("触发习气")

    return min(1.0, round(score, 4)), "、".join(reasons) or "自然浮现"


def _novelty_score(content: str, recent_texts: list[str]) -> float:
    normalized = _normalize_text(content)
    if not normalized or not recent_texts:
        return 0.5
    similarities = [_text_similarity(normalized, _normalize_text(text)) for text in recent_texts]
    highest_similarity = max(similarities) if similarities else 0.0
    return max(0.0, 1.0 - highest_similarity)


def _goal_relevance_score(content: str, goal_stack: list[str], note_text: str) -> float:
    haystack = _normalize_text(content)
    if not haystack:
        return 0.0
    tokens = _goal_tokens(goal_stack, note_text)
    if not tokens:
        return 0.0
    overlaps = sum(1 for token in tokens if token in haystack)
    return min(1.0, overlaps / max(1, min(5, len(tokens))))


def _emotion_resonance_score(content: str, emotion: EmotionSnapshot) -> float:
    dominant = emotion["dominant"]
    text = _normalize_text(content)
    if dominant == "curiosity" and any(token in text for token in ("为什么", "好奇", "想知道", "研究")):
        return 0.8
    if dominant == "calm" and any(token in text for token in ("静", "稳", "慢", "安")):
        return 0.7
    if dominant == "frustration" and any(token in text for token in ("卡住", "失败", "重复", "困住")):
        return 0.8
    if dominant == "satisfaction" and any(token in text for token in ("终于", "接住", "做到", "松一口气")):
        return 0.7
    if dominant == "concern" and any(token in text for token in ("回应", "等", "在吗", "接住")):
        return 0.8
    return 0.1


def _habit_resonance_score(content: str, active_habits: list[HabitPromptEntry]) -> float:
    text = _normalize_text(content)
    if not text:
        return 0.0
    matched_strength = 0.0
    for habit in active_habits:
        pattern = _normalize_text(habit["pattern"])
        if pattern and pattern in text:
            matched_strength = max(matched_strength, float(habit["strength"]))
    return matched_strength


def _goal_tokens(goal_stack: list[str], note_text: str) -> list[str]:
    tokens: list[str] = []
    for goal in goal_stack:
        tokens.extend(_extract_keyword_tokens(goal))
    tokens.extend(_extract_keyword_tokens(note_text))
    return tokens


def _extract_keyword_tokens(text: str) -> list[str]:
    compact = _normalize_text(text)
    if not compact:
        return []
    tokens = [token for token in GOAL_TOKEN_PATTERN if token in compact]
    tokens.extend(
        chunk
        for chunk in compact.split()
        if len(chunk) >= 2
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered[:8]


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
