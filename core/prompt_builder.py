"""Assemble the prompt for each thought-generation cycle."""

from dataclasses import dataclass
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime

from core.action import ActionRecord
from core.i18n import localized_thought_type, t
from core.stimulus import RECENT_CONVERSATION_SUMMARY_MAX_CHARS, Stimulus
from core.thought_parser import Thought
from core.common_types import (
    DegenerationIntervention,
    EmotionSnapshot,
    HabitPromptEntry,
    JsonObject,
    JsonValue,
    ManasPromptState,
    PrefrontalPromptState,
    RecentConversationPrompt,
    ReflectionPromptEntry,
    ReplyFocusPromptState,
    SleepStateSnapshot,
    detect_rewritten_repetition,
    elapsed_ms,
)

ACTION_MARKER_PATTERN = re.compile(r"\s*\{action:[^}]+\}", re.DOTALL)
ACTION_MARKER_SUFFIX_PATTERN = re.compile(r"\s*\{action:[^}]+\}\s*$")
ACTION_ECHO_ORIGIN = "action"
PENDING_ACTION_VISIBLE_STATUSES = {"pending"}
RUNNING_ACTION_VISIBLE_STATUSES = {"running"}
PROMPT_SECTION_LOG_THRESHOLD_MS = 10.0
STAGNATION_CHECK_CYCLES = 3
STAGNATION_SIMILARITY_THRESHOLD = 0.6
STAGNATION_MIN_MATCHED_THOUGHTS = 2
logger = logging.getLogger(__name__)


def _system_prompt_prefix() -> str:
    from core.i18n import prompt_block
    return str(prompt_block("SYSTEM_PROMPT_PREFIX"))


def _system_prompt_action_examples_prefix() -> tuple[str, ...]:
    from core.i18n import prompt_block
    block = prompt_block("SYSTEM_PROMPT_ACTION_EXAMPLES_PREFIX")
    assert isinstance(block, tuple)
    return block


def _system_prompt_implicit_send_examples() -> tuple[str, ...]:
    from core.i18n import prompt_block
    block = prompt_block("SYSTEM_PROMPT_IMPLICIT_SEND_MESSAGE_ACTION_EXAMPLES")
    assert isinstance(block, tuple)
    return block


def _system_prompt_action_examples_suffix() -> tuple[str, ...]:
    from core.i18n import prompt_block
    block = prompt_block("SYSTEM_PROMPT_ACTION_EXAMPLES_SUFFIX")
    assert isinstance(block, tuple)
    return block


def _system_prompt_suffix() -> str:
    from core.i18n import prompt_block
    return str(prompt_block("SYSTEM_PROMPT_SUFFIX"))


def _passive_stimulus_labels() -> dict[str, str]:
    return {
        "time": t("stimulus.label.time"),
        "system_status": t("stimulus.label.system_status"),
        "weather": t("stimulus.label.weather"),
        "news": t("stimulus.label.news"),
        "reading": t("stimulus.label.reading"),
    }


def _action_echo_labels() -> dict[str, str]:
    return {
        "get_time": t("stimulus.label.get_time"),
        "get_system_status": t("stimulus.label.get_system_status"),
        "news": t("stimulus.label.news"),
        "weather": t("stimulus.label.weather"),
        "reading": t("stimulus.label.reading"),
        "search": t("stimulus.label.search"),
        "web_fetch": t("stimulus.label.web_fetch"),
        "send_message": t("stimulus.label.send_message"),
        "note_rewrite": t("stimulus.label.note_rewrite"),
        "file_modify": t("stimulus.label.file_modify"),
        "system_change": t("stimulus.label.system_change"),
    }


def _stagnation_stopwords() -> set[str]:
    from core.i18n import stopwords as i18n_stopwords
    return i18n_stopwords("stagnation")


@dataclass(frozen=True)
class PromptBuildContext:
    manas_state: ManasPromptState | None = None
    emotion: EmotionSnapshot | None = None
    sleep_state: SleepStateSnapshot | None = None
    degeneration_intervention: DegenerationIntervention | None = None
    active_habits: list[HabitPromptEntry] | None = None
    prefrontal_state: PrefrontalPromptState | None = None
    recent_reflections: list[ReflectionPromptEntry] | None = None
    long_term_context: list[str] | None = None
    current_impressions: list[str] | None = None
    note_text: str = ""
    stimuli: list[Stimulus] | None = None
    recent_action_echoes: list[Stimulus] | None = None
    running_actions: list[ActionRecord] | None = None
    perception_cues: list[str] | None = None
    recent_conversations: list[RecentConversationPrompt] | None = None
    reply_focus: ReplyFocusPromptState | None = None
    visual_input_present: bool = False


def build_prompt(
    cycle_id: int,
    identity: dict[str, str],
    recent_thoughts: list[Thought],
    context_window: int,
    prompt_context: PromptBuildContext | None = None,
) -> str:
    """Build a single prompt string for thought generation."""
    resolved_context = prompt_context or PromptBuildContext()
    conversations, action_echoes, passive = _split_stimuli(resolved_context.stimuli or [])
    note_text_for_system = resolved_context.note_text
    parts = [
        _timed_prompt_section(
            "system",
            lambda: _build_system(
                identity,
                allow_implicit_send_message=bool(conversations or resolved_context.reply_focus),
                note_text=note_text_for_system,
            ),
        )
    ]
    visible_pending_actions = _visible_pending_actions(resolved_context.running_actions)
    visible_running_actions = _visible_running_actions(resolved_context.running_actions)
    window = recent_thoughts[-context_window * 3:]
    _append_prompt_context_sections(
        parts,
        resolved_context.manas_state,
        resolved_context.emotion,
        resolved_context.prefrontal_state,
        resolved_context.recent_reflections or [],
        window,
        resolved_context.long_term_context,
        resolved_context.note_text,
        resolved_context.perception_cues or [],
    )
    conversation_labels = _conversation_label_map(conversations, resolved_context.recent_conversations)
    _append_prompt_stimulus_sections(
        parts,
        conversations,
        action_echoes,
        resolved_context.recent_action_echoes or [],
        visible_pending_actions,
        visible_running_actions,
        passive,
        resolved_context.current_impressions or [],
        resolved_context.recent_conversations or [],
        resolved_context.visual_input_present,
        resolved_context.reply_focus,
        conversation_labels,
    )
    stagnation_warning = _stagnation_warning_text(
        window,
        resolved_context.long_term_context,
        resolved_context.note_text,
        resolved_context.recent_action_echoes or [],
        action_echoes,
        visible_pending_actions,
        visible_running_actions,
        passive,
        resolved_context.visual_input_present,
        resolved_context.perception_cues or [],
        resolved_context.recent_conversations or [],
        bool(conversations or action_echoes or visible_pending_actions or visible_running_actions),
    )
    if stagnation_warning:
        parts.append(stagnation_warning)
    intervention = resolved_context.degeneration_intervention
    if intervention is not None:
        active_intervention: DegenerationIntervention = intervention
        _append_prompt_section(
            parts,
            "degeneration_nudge",
            lambda: _format_degeneration_nudge(
                active_intervention,
                has_conversation=bool(conversations),
                has_external_results=bool(action_echoes or passive),
            ),
        )
    _append_prompt_section(parts, "next_cycle", lambda: _format_next_cycle(cycle_id))
    return "\n\n".join(parts)


def _append_prompt_context_sections(
    parts: list[str],
    manas_state: ManasPromptState | None,
    emotion: EmotionSnapshot | None,
    prefrontal_state: PrefrontalPromptState | None,
    recent_reflections: list[ReflectionPromptEntry],
    window: list[Thought],
    long_term_context: list[str] | None,
    note_text: str,
    perception_cues: list[str],
) -> None:
    if window:
        _append_prompt_section(parts, "recent_thoughts", lambda: _format_thought_history(window))
    if long_term_context:
        ltm = long_term_context
        _append_prompt_section(parts, "long_term", lambda: _format_long_term(ltm))
    if prefrontal_state is not None and _prefrontal_needs_prompt(prefrontal_state):
        executive = prefrontal_state
        _append_prompt_section(parts, "goal_stack", lambda: _format_prefrontal_alert(executive))
    if manas_state and _manas_needs_prompt(manas_state):
        current_manas = manas_state
        _append_prompt_section(parts, "manas", lambda: _format_manas(current_manas))
    emotion_alert = _emotion_alert(emotion) if emotion else ""
    if emotion_alert:
        _append_prompt_section(parts, "emotion", lambda: emotion_alert)
    if recent_reflections:
        reflections = recent_reflections
        _append_prompt_section(parts, "reflections", lambda: _format_recent_reflections(reflections))
    if note_text.strip():
        _append_prompt_section(parts, "note", lambda: _format_note(note_text))
    if perception_cues:
        cues = perception_cues
        _append_prompt_section(parts, "perception_cues", lambda: _format_perception_cues(cues))


def _append_prompt_stimulus_sections(
    parts: list[str],
    conversations: list[Stimulus],
    action_echoes: list[Stimulus],
    recent_action_echoes: list[Stimulus],
    visible_pending_actions: list[ActionRecord],
    visible_running_actions: list[ActionRecord],
    passive: list[Stimulus],
    current_impressions: list[str],
    recent_conversations: list[RecentConversationPrompt],
    visual_input_present: bool,
    reply_focus: ReplyFocusPromptState | None,
    conversation_labels: dict[str, str],
) -> None:
    if action_echoes or recent_action_echoes:
        _append_prompt_section(
            parts,
            "action_echoes",
            lambda: _format_action_echoes(
                recent_action_echoes,
                action_echoes,
                conversation_labels,
            ),
        )
    if visible_pending_actions:
        _append_prompt_section(
            parts,
            "pending_actions",
            lambda: _format_pending_actions(visible_pending_actions, conversation_labels),
        )
    if visible_running_actions:
        _append_prompt_section(
            parts,
            "running_actions",
            lambda: _format_running_actions(visible_running_actions, conversation_labels),
        )
    if passive:
        _append_prompt_section(parts, "passive_stimuli", lambda: _format_sensory_stimuli(passive))
    if current_impressions:
        impressions = current_impressions
        _append_prompt_section(parts, "impressions", lambda: _format_impressions(impressions))
    if recent_conversations:
        convos = recent_conversations
        _append_prompt_section(
            parts,
            "recent_conversations",
            lambda: _format_recent_conversations(convos),
        )
    if visual_input_present:
        _append_prompt_section(
            parts,
            "visual_input",
            lambda: _format_visual_input(has_conversation=bool(conversations)),
        )
    if not conversations and reply_focus is not None:
        focus = reply_focus
        _append_prompt_section(parts, "reply_focus", lambda: _format_reply_focus(focus, conversation_labels))
    if conversations:
        _append_prompt_section(parts, "conversations", lambda: _format_conversations(conversations))


def _stagnation_warning_text(
    window: list[Thought],
    long_term_context: list[str] | None,
    note_text: str,
    recent_action_echoes: list[Stimulus],
    action_echoes: list[Stimulus],
    visible_pending_actions: list[ActionRecord],
    visible_running_actions: list[ActionRecord],
    passive: list[Stimulus],
    visual_input_present: bool,
    perception_cues: list[str],
    recent_conversations: list[RecentConversationPrompt],
    has_foreground: bool,
) -> str:
    if not window:
        return ""
    return _detect_thought_stagnation(
        window,
        available_sources=_stagnation_sources(
            long_term_context,
            note_text,
            recent_action_echoes,
            action_echoes,
            visible_pending_actions,
            visible_running_actions,
            passive,
            visual_input_present,
            perception_cues,
            recent_conversations,
        ),
        has_foreground=has_foreground,
    )


def _append_prompt_section(
    parts: list[str],
    section_name: str,
    builder: Callable[[], str],
) -> None:
    section = _timed_prompt_section(section_name, builder)
    if section:
        parts.append(section)


def _timed_prompt_section(section_name: str, builder: Callable[[], str]) -> str:
    started_at = time.perf_counter()
    section = str(builder() or "")
    elapsed = elapsed_ms(started_at)
    if elapsed >= PROMPT_SECTION_LOG_THRESHOLD_MS:
        logger.info(
            "prompt section %s built in %.1f ms (chars=%d)",
            section_name,
            elapsed,
            len(section),
        )
    return section


def _build_system(
    identity: dict[str, str],
    *,
    allow_implicit_send_message: bool,
    note_text: str = "",
) -> str:
    identity_title = t("prompt.section.identity")
    parts = [_system_prompt_text(allow_implicit_send_message, note_text=note_text), f"## {identity_title}"]
    for content in identity.values():
        normalized = content.strip()
        if normalized:
            parts.append(normalized)
    return "\n\n".join(parts)


def _system_prompt_text(allow_implicit_send_message: bool, *, note_text: str = "") -> str:
    action_examples = list(_system_prompt_action_examples_prefix())
    if allow_implicit_send_message:
        action_examples.extend(_system_prompt_implicit_send_examples())
    action_examples.extend(_system_prompt_action_examples_suffix())
    return "\n".join(
        [
            _system_prompt_prefix().rstrip(),
            *action_examples,
            "",
            _system_prompt_suffix_with_note_warning(note_text),
        ]
    ).rstrip()


def _prefrontal_needs_prompt(prefrontal_state: PrefrontalPromptState | None) -> bool:
    if prefrontal_state is None:
        return False
    return bool(prefrontal_state.get("guidance") or prefrontal_state.get("inhibition_notes"))


def _format_prefrontal_alert(prefrontal_state: PrefrontalPromptState) -> str:
    lines: list[str] = []
    for guidance in prefrontal_state.get("guidance") or []:
        lines.append(f"- {guidance}")
    if prefrontal_state.get("inhibition_notes"):
        if lines:
            lines.append("")
        lines.append(t("prefrontal.inhibited_header"))
        for note in prefrontal_state["inhibition_notes"]:
            lines.append(f"- {note}")
    return _render_section(t("prompt.section.prefrontal"), lines, keep_blank_lines=True)


def _manas_needs_prompt(manas_state: ManasPromptState) -> bool:
    return bool(
        str(manas_state.get("session_context") or "").strip()
        or str(manas_state.get("identity_notice") or "").strip()
        or str(manas_state.get("warning") or "").strip()
        or manas_state.get("reflection_requested")
    )


def _format_manas(manas_state: ManasPromptState) -> str:
    lines: list[str] = []
    session_context = str(manas_state.get("session_context") or "").strip()
    if session_context:
        lines.append(session_context)
    identity_notice = str(manas_state.get("identity_notice") or "").strip()
    if identity_notice:
        lines.append(identity_notice)
    warning = str(manas_state.get("warning") or "").strip()
    if warning:
        lines.append(warning)
    if manas_state.get("reflection_requested"):
        lines.append(t("manas.reflection_needed"))
    return _render_section(t("prompt.section.manas"), lines)


EMOTION_ALERT_THRESHOLD = 0.65


def _emotion_alert_labels() -> dict[str, str]:
    return {
        "frustration": t("emotion.alert.frustration"),
        "concern": t("emotion.alert.concern"),
        "curiosity": t("emotion.alert.curiosity"),
        "satisfaction": t("emotion.alert.satisfaction"),
        "calm": t("emotion.alert.calm"),
    }


def _emotion_alert(emotion: EmotionSnapshot) -> str:
    dimensions = emotion.get("dimensions") or {}
    alerts: list[str] = []
    for dimension, value in sorted(dimensions.items(), key=lambda item: item[1], reverse=True):
        if float(value) >= EMOTION_ALERT_THRESHOLD:
            label = _emotion_alert_labels().get(dimension)
            if label:
                alerts.append(label)
    if not alerts:
        return ""
    return "\n".join(alerts)


def _format_recent_reflections(reflections: list[ReflectionPromptEntry]) -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for reflection in reflections:
        content = reflection["content"].strip()
        if content and content not in seen:
            seen.add(content)
            lines.append(f"- {content}")
    return _render_section(t("prompt.section.recent_reflections"), lines)


def _format_long_term(memories: list[str]) -> str:
    lines = []
    for mem in memories:
        lines.append(f"- {_compact_prompt_text(mem)}")
    return _render_section(t("prompt.section.long_term"), lines)


def _format_impressions(impressions: list[str]) -> str:
    lines = [f"- {_compact_prompt_text(impression)}" for impression in impressions]
    return _render_section(t("prompt.section.impressions"), lines)


NOTE_SOFT_LIMIT = 1000
NOTE_SEVERE_LIMIT = 1500


def _format_note(note_text: str) -> str:
    return _render_section(t("prompt.section.note"), [str(note_text).strip()], keep_blank_lines=True)


def _note_length_warning(note_text: str) -> str:
    note_len = len(note_text.strip())
    if note_len > NOTE_SEVERE_LIMIT:
        return t("prompt.note_warning_severe", note_len=note_len, limit=NOTE_SOFT_LIMIT)
    if note_len > NOTE_SOFT_LIMIT:
        return t("prompt.note_warning", note_len=note_len, limit=NOTE_SOFT_LIMIT)
    return ""


def _system_prompt_suffix_with_note_warning(note_text: str) -> str:
    suffix = _system_prompt_suffix().strip()
    note_warning = _note_length_warning(note_text)
    if not note_warning:
        return suffix
    examples_marker = t("prompt.section.examples_marker")
    note_intro, separator, remainder = suffix.partition(f"\n\n## {examples_marker}")
    if not separator:
        return f"{suffix}\n{note_warning}"
    return f"{note_intro}\n{note_warning}\n\n## {examples_marker}{remainder}"


def _split_stimuli(stimuli: list[Stimulus]) -> tuple[list[Stimulus], list[Stimulus], list[Stimulus]]:
    conversations = []
    action_echoes = []
    passive = []
    for stimulus in stimuli:
        if stimulus.type == "conversation":
            conversations.append(stimulus)
        elif _is_action_echo(stimulus):
            action_echoes.append(stimulus)
        else:
            passive.append(stimulus)
    return conversations, action_echoes, passive


def _format_conversations(conversations: list[Stimulus]) -> str:
    lines = [
        t("prompt.conversation.foreground_hint"),
        t("prompt.conversation.send_hint"),
        t("prompt.conversation.implicit_target_hint"),
        "",
    ]
    for conv in conversations:
        lines.append(_format_conversation_line(conv))
    return _render_section(t("prompt.section.conversations"), lines, keep_blank_lines=True)


def _format_reply_focus(reply_focus: ReplyFocusPromptState, conversation_labels: dict[str, str]) -> str:
    target = _known_target_label(reply_focus["source"], conversation_labels)
    lines = [
        t("prompt.reply_focus.no_new_messages", target=target),
        t("prompt.reply_focus.default_target"),
    ]
    return _render_section(t("prompt.section.reply_focus"), lines)


def _format_recent_conversations(conversations: list[RecentConversationPrompt]) -> str:
    lines: list[str] = []
    for conversation in conversations:
        last_time = _recent_conversation_local_time(conversation["last_timestamp"])
        lines.append(t("prompt.recent_conv.header", source_label=conversation["source_label"], last_time=last_time))
        lines.append("")
        summary = _truncate_conversation_summary(str(conversation.get("summary") or "").strip())
        if summary:
            lines.append(t("prompt.recent_conv.summary_prefix", summary=summary))
            lines.append("")
        for message in conversation["messages"]:
            content = _compact_prompt_text(message["content"])
            if content:
                lines.append(
                    t(
                        "prompt.format.speaker_line",
                        speaker=message["speaker_name"],
                        content=content,
                    )
                )
        lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return _render_section(t("prompt.section.recent_conversations"), lines, keep_blank_lines=True)


def _format_degeneration_nudge(
    intervention: DegenerationIntervention,
    *,
    has_conversation: bool,
    has_external_results: bool,
) -> str:
    lines = [t("degeneration.nudge.must_act")]
    if intervention["must_externalize"]:
        lines.append(t("degeneration.nudge.exclude_note"))
    if has_conversation:
        lines.append(t("degeneration.nudge.prefer_conversation"))
    elif has_external_results:
        lines.append(t("degeneration.nudge.prefer_results"))
    elif intervention["suggestions"]:
        lines.append(t("degeneration.nudge.prefer_suggestion", suggestion=intervention['suggestions'][0]))
    return _render_section(t("prompt.section.degeneration_nudge"), lines)


def _format_sensory_stimuli(stimuli: list[Stimulus]) -> str:
    lines = []
    for stimulus in stimuli:
        lines.append(
            f"- {_passive_stimulus_label(stimulus.type)} {_compact_prompt_text(stimulus.content)}"
        )
    return _render_section(t("prompt.section.passive_stimuli"), lines)


def _format_action_echoes(
    recent_stimuli: list[Stimulus],
    current_stimuli: list[Stimulus],
    conversation_labels: dict[str, str],
) -> str:
    lines: list[str] = []
    if recent_stimuli:
        lines.append(t("prompt.action_echoes.recent_header"))
        lines.append("")
        lines.extend(_action_echo_lines(recent_stimuli, conversation_labels))
    if recent_stimuli or current_stimuli:
        if lines:
            lines.append("")
        lines.append(t("prompt.action_echoes.current_header"))
        lines.append("")
        if current_stimuli:
            lines.extend(_action_echo_lines(current_stimuli, conversation_labels))
        else:
            lines.append(t("prompt.action_echoes.none"))
    return _render_section(t("prompt.section.action_echoes"), lines, keep_blank_lines=True)


def _action_echo_lines(stimuli: list[Stimulus], conversation_labels: dict[str, str]) -> list[str]:
    return [
        f"- {_action_echo_label(stimulus)} {_action_echo_text(stimulus, conversation_labels)}"
        for stimulus in stimuli
    ]


def _format_thought_history(thoughts: list[Thought]) -> str:
    lines = []
    current_cycle = -1
    for thought in thoughts:
        if thought.cycle_id != current_cycle:
            current_cycle = thought.cycle_id
            lines.append(t("prompt.cycle_header", cycle_id=thought.cycle_id))
        trigger = f" (← {thought.trigger_ref})" if thought.trigger_ref else ""
        content = _strip_action_markers(thought.content) or thought.content
        display_type = localized_thought_type(thought.type)
        lines.append(f"[{display_type}-{thought.thought_id}] {content}{trigger}")
    return _render_section(t("prompt.section.recent_thoughts"), lines)


def _detect_thought_stagnation(
    thoughts: list[Thought],
    *,
    available_sources: list[str],
    has_foreground: bool,
) -> str:
    if len(thoughts) < STAGNATION_CHECK_CYCLES * 3:
        return ""
    recent_cycles = _group_recent_cycles(thoughts, STAGNATION_CHECK_CYCLES)
    if len(recent_cycles) < STAGNATION_CHECK_CYCLES:
        return ""
    normalized_cycles = [
        [
            normalized
            for thought in cycle_thoughts
            if thought.type != "reflection"
            for normalized in (_normalize_stagnation_text(thought.content),)
            if normalized
        ]
        for cycle_thoughts in recent_cycles
    ]
    if any(not cycle_texts for cycle_texts in normalized_cycles):
        return ""
    cycle_texts = [
        " ".join(cycle_thoughts)
        for cycle_thoughts in normalized_cycles
    ]
    if _stagnation_detected(normalized_cycles):
        return _stagnation_warning(cycle_texts, available_sources, has_foreground)
    return ""


def _group_recent_cycles(thoughts: list[Thought], n: int) -> list[list[Thought]]:
    cycles: dict[int, list[Thought]] = {}
    for thought in thoughts:
        cycles.setdefault(thought.cycle_id, []).append(thought)
    sorted_cycle_ids = sorted(cycles.keys())[-n:]
    return [cycles[cid] for cid in sorted_cycle_ids]


def _stagnation_detected(cycle_texts: list[list[str]]) -> bool:
    return detect_rewritten_repetition(
        cycle_texts,
        similarity_threshold=STAGNATION_SIMILARITY_THRESHOLD,
        min_matched_texts=STAGNATION_MIN_MATCHED_THOUGHTS,
    )


def _stagnation_sources(
    long_term_context: list[str] | None,
    note_text: str,
    recent_action_echoes: list[Stimulus],
    action_echoes: list[Stimulus],
    pending_actions: list[ActionRecord],
    running_actions: list[ActionRecord],
    passive: list[Stimulus],
    visual_input_present: bool,
    perception_cues: list[str],
    recent_conversations: list[RecentConversationPrompt],
) -> list[str]:
    sources: list[str] = []
    if long_term_context:
        sources.append(t("prompt.section.long_term"))
    if note_text.strip():
        sources.append(t("prompt.section.note"))
    if recent_action_echoes or action_echoes:
        sources.append(t("prompt.section.action_echoes"))
    if pending_actions:
        sources.append(t("prompt.section.pending_actions"))
    if running_actions:
        sources.append(t("prompt.section.running_actions"))
    if passive:
        sources.append(t("prompt.section.passive_stimuli"))
    if visual_input_present:
        sources.append(t("prompt.section.visual_input"))
    if perception_cues:
        sources.append(t("prompt.section.perception_cues"))
    _ = recent_conversations
    return sources


def _normalize_stagnation_text(text: str) -> str:
    stripped = _strip_action_markers(text)
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", stripped)
    return _compact_prompt_text(normalized)


def _stagnation_warning(
    cycle_texts: list[str],
    available_sources: list[str],
    has_foreground: bool,
) -> str:
    repeated_terms = _stagnation_terms(cycle_texts)
    if repeated_terms:
        repeated_text = t("stagnation.repeated_terms", terms=", ".join(repeated_terms))
    else:
        repeated_text = t("stagnation.repeated_generic")
    if available_sources:
        source_text = t("prompt.format.source_separator").join(available_sources)
    else:
        source_text = t("stagnation.generic_source")
    if has_foreground:
        return (
            t("stagnation.warning_prefix_foreground")
            + repeated_text
            + t("stagnation.require_new_source_foreground", sources=source_text)
        )
    return (
        t("stagnation.warning_prefix")
        + repeated_text
        + t("stagnation.require_new_source", sources=source_text)
    )


def _stagnation_terms(cycle_texts: list[str]) -> list[str]:
    counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    next_index = 0
    for text in cycle_texts:
        terms = _stagnation_term_candidates(text)
        counts.update(terms)
        for term in terms:
            if term not in first_seen:
                first_seen[term] = next_index
                next_index += 1
    ranked_terms = [
        term for term, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], first_seen[item[0]]),
        )
        if count >= 2
    ]
    return ranked_terms[:5]


def _stagnation_term_candidates(text: str) -> list[str]:
    terms: list[str] = []
    seen_terms: set[str] = set()
    for clause in text.split():
        candidate = clause.strip()
        if not candidate:
            continue
        candidate = _trim_stagnation_prefix(candidate)
        if len(candidate) < 2:
            continue
        if candidate in _stagnation_stopwords():
            continue
        if len(candidate) > 24:
            candidate = f"{candidate[:24].rstrip()}..."
        if candidate in seen_terms:
            continue
        terms.append(candidate)
        seen_terms.add(candidate)
    return terms


def _trim_stagnation_prefix(candidate: str) -> str:
    trimmed = candidate
    while True:
        if len(trimmed) >= 3 and trimmed[:1] in {"和", "与"}:
            trimmed = trimmed[1:]
            continue
        lowered = trimmed.lower()
        if lowered.startswith("and "):
            trimmed = trimmed[4:].lstrip()
            continue
        break
    return trimmed


def _format_running_actions(actions: list[ActionRecord], conversation_labels: dict[str, str]) -> str:
    lines = []
    for action in actions:
        lines.append(
            f"- [{action.type}/{action.status}] {_running_action_summary(action, conversation_labels)}"
        )
    return _render_section(t("prompt.section.running_actions"), lines)


def _format_pending_actions(actions: list[ActionRecord], conversation_labels: dict[str, str]) -> str:
    lines = []
    for action in actions:
        lines.append(
            f"- [{action.type}/{action.status}] {_pending_action_summary(action, conversation_labels)}"
        )
    return _render_section(t("prompt.section.pending_actions"), lines)


def _visible_pending_actions(actions: list[ActionRecord] | None) -> list[ActionRecord]:
    return [
        action for action in (actions or []) if action.status in PENDING_ACTION_VISIBLE_STATUSES
    ]


def _visible_running_actions(actions: list[ActionRecord] | None) -> list[ActionRecord]:
    return [
        action for action in (actions or []) if action.status in RUNNING_ACTION_VISIBLE_STATUSES
    ]


def _format_perception_cues(cues: list[str]) -> str:
    return _render_section(t("prompt.section.perception_cues"), [f"- {cue}" for cue in cues])


def _format_visual_input(*, has_conversation: bool) -> str:
    lines = [
        t("visual.description"),
        t("visual.natural_only"),
    ]
    if has_conversation:
        lines.append(t("visual.conversation_priority"))
    return _render_section(t("prompt.section.visual_input"), lines)


def _format_next_cycle(cycle_id: int) -> str:
    return f"## {t('prompt.section.next_cycle')}\n\n{t('prompt.cycle_header', cycle_id=cycle_id)}"


def _render_section(title: str, lines: list[str], *, keep_blank_lines: bool = False) -> str:
    if keep_blank_lines:
        body = "\n".join(lines)
    else:
        body = "\n".join(line for line in lines if line)
    return f"## {title}\n\n{body}"


def _passive_stimulus_label(stimulus_type: str) -> str:
    return _passive_stimulus_labels().get(stimulus_type, t("stimulus.label.fallback"))


def _conversation_prefix(stimulus: Stimulus) -> str:
    parts = [_conversation_speaker(stimulus)]
    message_id = stimulus.metadata.get("telegram_message_id")
    if message_id:
        parts.append(f"[msg:{message_id}]")
    reply_context = _conversation_reply_context(stimulus)
    if reply_context:
        parts.append(reply_context)
    return " ".join(str(part).strip() for part in parts if str(part).strip())


def _format_conversation_line(stimulus: Stimulus) -> str:
    merged_messages = stimulus.metadata.get("merged_messages")
    if isinstance(merged_messages, list) and len(merged_messages) > 1:
        return "\n\n".join(
            _format_conversation_block(message) for message in merged_messages
        )
    content = _compact_prompt_text(stimulus.content)
    return t("prompt.conversation.say", prefix=_conversation_prefix(stimulus), content=content)


def _format_conversation_block(message: JsonValue) -> str:
    if not isinstance(message, dict):
        return _compact_prompt_text(str(message or ""))
    content = _compact_prompt_text(str(message.get("content") or ""))
    prefix = _conversation_dict_prefix(message)
    if not prefix:
        return content
    if not content:
        return prefix
    return f"{t('prompt.conversation.say_block', prefix=prefix)}\n{content}"


def _conversation_dict_prefix(message: JsonObject) -> str:
    parts = [_conversation_dict_speaker(message)]
    message_id = message.get("telegram_message_id")
    if message_id:
        parts.append(f"[msg:{message_id}]")
    reply_context = _conversation_dict_reply_context(message)
    if reply_context:
        parts.append(reply_context)
    return " ".join(str(part).strip() for part in parts if str(part).strip())


def _conversation_dict_speaker(message: JsonObject) -> str:
    source = str(message.get("source") or "").strip()
    metadata = _conversation_metadata_from_dict(message)
    return _person_label(source, metadata)


def _conversation_dict_reply_context(message: JsonObject) -> str:
    reply_to_message_id = message.get("reply_to_message_id")
    reply_preview = _compact_prompt_text(str(message.get("reply_to_preview") or ""))
    if not reply_to_message_id or not reply_preview:
        return ""
    if bool(message.get("reply_to_from_self")):
        quoted_owner = t("prompt.conversation.quote_self")
    else:
        quoted_owner = t("prompt.conversation.quote_other")
    return t(
        "prompt.format.quote_context",
        owner=quoted_owner,
        message_id=reply_to_message_id,
        preview=reply_preview,
    )


def _conversation_speaker(stimulus: Stimulus) -> str:
    return _person_label(stimulus.source, stimulus.metadata)


def _conversation_reply_context(stimulus: Stimulus) -> str:
    reply_to_message_id = stimulus.metadata.get("reply_to_message_id")
    reply_preview = _compact_prompt_text(str(stimulus.metadata.get("reply_to_preview") or ""))
    if not reply_to_message_id or not reply_preview:
        return ""
    if bool(stimulus.metadata.get("reply_to_from_self")):
        quoted_owner = t("prompt.conversation.quote_self")
    else:
        quoted_owner = t("prompt.conversation.quote_other")
    return t(
        "prompt.format.quote_context",
        owner=quoted_owner,
        message_id=reply_to_message_id,
        preview=reply_preview,
    )


def _action_echo_label(stimulus: Stimulus) -> str:
    action_type = str(stimulus.metadata.get("action_type") or "").strip()
    if action_type:
        return _action_echo_labels().get(action_type, t("stimulus.label.unknown"))
    return t("stimulus.label.unknown")


def _is_action_echo(stimulus: Stimulus) -> bool:
    origin = str(stimulus.metadata.get("origin") or "").strip()
    if origin == ACTION_ECHO_ORIGIN:
        return True
    return stimulus.source.startswith("action:") or stimulus.source.startswith("planner:")


def _running_action_summary(action: ActionRecord, conversation_labels: dict[str, str]) -> str:
    if action.type == "send_message":
        return _running_send_message_summary(action, conversation_labels)
    task_summary = _running_action_task_summary(action)
    if task_summary:
        return task_summary
    summary = str(action.request.get("reason") or "").strip() or action.type
    return _compact_prompt_text(_strip_action_marker(summary))


def _pending_action_summary(action: ActionRecord, conversation_labels: dict[str, str]) -> str:
    summary = _running_action_summary(action, conversation_labels)
    if action.awaiting_confirmation:
        return t("prompt.pending.awaiting_confirm", summary=summary)
    if action.retry_after is not None:
        return t("prompt.pending.awaiting_retry", summary=summary)
    return t("prompt.pending.awaiting_exec", summary=summary)


def _running_send_message_summary(action: ActionRecord, conversation_labels: dict[str, str]) -> str:
    target = _known_target_label(
        str(
            action.request.get("target_source")
            or action.request.get("target_entity")
            or t("action.default_target_label")
        ).strip(),
        conversation_labels,
    )
    message = _running_message_excerpt(str(action.request.get("message_text") or ""))
    if message:
        return t("prompt.send.message_with_excerpt", target=target, message=message)
    task_summary = _running_action_task_summary(action)
    if task_summary:
        return task_summary
    return t("prompt.send.message_only", target=target)


def _running_action_task_summary(action: ActionRecord) -> str:
    task = str(action.request.get("task") or "").strip()
    if not task:
        return ""
    first_line = next((line.strip() for line in task.splitlines() if line.strip()), "")
    if not first_line:
        return ""
    return _compact_prompt_text(_strip_action_marker(first_line))


def _running_message_excerpt(message: str) -> str:
    return _compact_prompt_text(message)


def _strip_action_marker(content: str) -> str:
    return ACTION_MARKER_SUFFIX_PATTERN.sub("", content).strip()


def _strip_action_markers(content: str) -> str:
    return ACTION_MARKER_PATTERN.sub("", content).strip()


def _compact_prompt_text(text: str) -> str:
    return " ".join(str(text).split())


def _action_echo_text(stimulus: Stimulus, conversation_labels: dict[str, str]) -> str:
    action_type = str(stimulus.metadata.get("action_type") or "").strip()
    if action_type != "send_message":
        return _compact_prompt_text(stimulus.content)
    result = stimulus.metadata.get("result")
    if not isinstance(result, dict):
        return _compact_prompt_text(stimulus.content)
    succeeded = bool(result.get("ok", True))
    summary = _compact_prompt_text(str(result.get("summary") or stimulus.content))
    data = result.get("data")
    if not isinstance(data, dict):
        return _compact_prompt_text(stimulus.content)
    target = _known_target_label(
        str(data.get("source") or data.get("target_entity") or "").strip(),
        conversation_labels,
    )
    message = _running_message_excerpt(str(data.get("message") or ""))
    if succeeded and target and message:
        return t("action.send_success_with_excerpt", target=target, excerpt=message)
    if succeeded and target:
        return t("action.send_success", target=target)
    if target and message:
        return t("action.send_fail_target_excerpt", target=target, excerpt=message, summary=summary)
    if message:
        return t("action.send_fail_excerpt", excerpt=message, summary=summary)
    if target:
        return t("action.send_fail_target", target=target, summary=summary)
    return _compact_prompt_text(stimulus.content)


def _conversation_label_map(
    conversations: list[Stimulus],
    recent_conversations: list[RecentConversationPrompt] | None,
) -> dict[str, str]:
    labels: dict[str, str] = {}
    for conversation in recent_conversations or []:
        source = str(conversation.get("source") or "").strip()
        label = str(conversation.get("source_label") or "").strip()
        if source and label:
            labels[source] = label
    for conversation in conversations:
        label = _conversation_speaker(conversation)
        if conversation.source and label:
            labels[conversation.source] = label
    return labels


def _known_target_label(target: str, conversation_labels: dict[str, str]) -> str:
    normalized = str(target or "").strip()
    return conversation_labels.get(normalized, normalized)


def _truncate_conversation_summary(summary: str) -> str:
    if not summary or len(summary) <= RECENT_CONVERSATION_SUMMARY_MAX_CHARS:
        return summary
    return summary[: RECENT_CONVERSATION_SUMMARY_MAX_CHARS - 3].rstrip() + "..."


def _recent_conversation_local_time(raw_timestamp: str) -> str:
    try:
        timestamp = datetime.fromisoformat(str(raw_timestamp).strip())
    except ValueError:
        return str(raw_timestamp).strip()
    return timestamp.astimezone().strftime("%Y-%m-%d %H:%M")


def _conversation_metadata_from_dict(message: dict) -> JsonObject:
    metadata: JsonObject = {}
    for key in ("telegram_full_name", "telegram_username"):
        value = str(message.get(key) or "").strip()
        if value:
            metadata[key] = value
    return metadata


def _person_label(source: str, metadata: JsonObject) -> str:
    full_name = str(metadata.get("telegram_full_name") or "").strip()
    username = str(metadata.get("telegram_username") or "").strip()
    display_name = full_name or username or source
    if source.startswith("telegram:"):
        return f"[{display_name}]({source})"
    return display_name
