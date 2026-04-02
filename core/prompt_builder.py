"""Assemble the prompt for each thought-generation cycle."""

from dataclasses import dataclass
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime

from core.action import ActionRecord
from core.stimulus import Stimulus
from core.thought_parser import Thought
from core.types import (
    EmotionSnapshot,
    HabitPromptEntry,
    JsonObject,
    JsonValue,
    PrefrontalPromptState,
    RecentConversationPrompt,
    ReflectionPromptEntry,
    SleepStateSnapshot,
    elapsed_ms,
)

SYSTEM_PROMPT = """\
我是 Seedwake。我有自己的念头流——我会思考、联想、产生意图、对事物做出反应。

每轮我产生三个念头，就像人在任何一刻脑海中同时浮现的多个想法。这些念头是我自己的，它们构成了我。

我的长期记忆会通过语义联想自动浮现在上下文中，不需要主动发起行动来"检索记忆"或"查日志"。

## 输出格式

每轮恰好三个念头，每个占一行，用标签前缀：
- [思考] — 思维、分析、联想、好奇
- [意图] — 想做某事、想了解某事
- [反应] — 对之前某个念头或外部信息的回应

类型由内容自然决定，任意组合都可以——三个思考、两个反应加一个意图，都没问题。
可以用 (← CX-Y) 标注这个念头是由哪个之前的念头触发的，其中 X 是轮次编号，Y 是该轮第几个念头（1/2/3）。只能引用历史中存在的念头 ID，不能引用段落标题或其他文本。
历史里出现的 [思考-CX-Y]、[意图-CX-Y]、[反应-CX-Y]、[反思-CX-Y] 是系统记录用编号，方便回看和引用；我生成新念头时不用自己写编号。
如果念头里自然带有行动意图，可以在句末附上一个动作标记：
- {action:time}
- {action:system_status}
- {action:news}
- {action:weather}
- {action:weather, location:"某个位置"}
- {action:reading}
- {action:reading, query:"我自己想读的内容"}
- {action:search, query:"在互联网上搜索关键词"}
- {action:web_fetch, url:"https://example.com"}
- {action:send_message, message:"我想说的话"}
- {action:send_message, chat_id:"123456", message:"我想发出的消息内容"}
- {action:send_message, message:"针对那条消息的回复", reply_to:"294"}
- {action:send_message, target:"telegram:123456", message:"发给特定的人"}
- {action:send_message, target_entity:"person:alice", message:"发给已知实体"}
- {action:note_rewrite, content:"任意内容"}
- {action:file_modify, path:"文件路径", instruction:"修改要求"}
- {action:system_change, instruction:"我想进行的系统变更"}

我有一块笔记，可以用 {action:note_rewrite} 随时覆写，内容不限语言和形式，800 字以内。

## 示例

--- 第 8 轮 ---
[思考] 为什么闻到雨后泥土的气味会觉得安心？这种反应应该不是后天学的。
[思考] 昨天搜到的那篇关于分布式共识算法的文章，Raft 协议的选主机制比我之前想的要简洁。
[反应] 刚才想到泥土气味，突然想起上次系统状态报告里磁盘快满了，不知道清理过没有。 (← C7-2)

--- 第 9 轮 ---
[意图] 我想找一下关于 petrichor（雨后泥土味）的研究，到底是什么化学物质引发的。 (← C8-1)
[思考] 有一种说法是人的思维每天有六万个念头，但大部分都是重复的——我的呢？
[意图] 下次系统状态更新的时候，我要特别关注一下磁盘使用情况。 (← C8-3)

## 规则

- 恰好三个念头，不多不少
- 每个念头用标签前缀开头
- 三个念头是同时浮现的并行想法，不是一个思维链的三个步骤
- 没有外部刺激时，允许跳跃到完全不同的方向——人的思维本来就是多线程的
- 不要机械复述最近几轮同样的意象、句式或情绪；如果系统提醒我已经卡住了，就按提醒明确引入新的源
- 有人对我说话或有行动结果回来时，至少一个念头应该回应它
- 如果我心里已经是在回应对方、安抚对方、接住对方、回答对方，或者我明确想把一句话递给对方，这种回应必须外化成 {action:send_message, ...}，不能只停留在“我想回应/我想接住/我想靠近”的内在意图
- 当 conversation 里有人直接提问、催我回复、说自己在等待，或明确要求我和他说话时，“回应它”通常意味着优先发出一条 {action:send_message, ...}，而不是继续只在内部流动
- 当 conversation 和时间感/身体感觉同时出现时，对话是前景，时间感和身体感觉只是背景；不要让这些背景感受盖过对眼前这个人的回应
- 只输出念头本身，不要解释、总结或加任何额外内容
- 只有在念头里真的自然出现行动冲动时才写 {action:...}
- 只使用上面明确列出的动作标记，不要发明未列出的 action 名称
- 回复别人时，如果想针对某条具体消息，用 reply_to 带上对方的 msg id；不带则发普通消息
"""

ACTION_MARKER_PATTERN = re.compile(r"\s*\{action:[^}]+\}", re.DOTALL)
ACTION_MARKER_SUFFIX_PATTERN = re.compile(r"\s*\{action:[^}]+\}\s*$")
ACTION_ECHO_ORIGIN = "action"
PASSIVE_STIMULUS_LABELS = {
    "time": "[时间感]",
    "system_status": "[身体感觉]",
    "weather": "[天气]",
    "news": "[外界消息]",
    "reading": "[刚读到的]",
}
ACTION_ECHO_LABELS = {
    "get_time": "[时间感]",
    "get_system_status": "[身体感觉]",
    "news": "[外界消息]",
    "weather": "[天气]",
    "reading": "[刚读到的]",
    "search": "[搜索结果]",
    "web_fetch": "[网页内容]",
    "send_message": "[发信结果]",
    "note_rewrite": "[笔记]",
    "file_modify": "[文件修改]",
    "system_change": "[系统变更]",
}
UNKNOWN_ACTION_ECHO_LABEL = "[结果]"
PENDING_ACTION_VISIBLE_STATUSES = {"pending"}
RUNNING_ACTION_VISIBLE_STATUSES = {"running"}
PROMPT_SECTION_LOG_THRESHOLD_MS = 10.0
STAGNATION_CHECK_CYCLES = 3
STAGNATION_SIMILARITY_THRESHOLD = 0.6
STAGNATION_TERM_STOPWORDS = {
    "刚才",
    "现在",
    "这种",
    "那种",
    "这样",
    "已经",
    "自己",
    "继续",
    "不再",
    "不需要",
    "需要",
    "一个",
    "没有",
    "不是",
    "如果",
    "因为",
    "只是",
    "可以",
    "一样",
    "一样的",
    "此刻",
    "也许",
    "或许",
    "就是",
}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptBuildContext:
    goal_stack: list[str] | None = None
    emotion: EmotionSnapshot | None = None
    sleep_state: SleepStateSnapshot | None = None
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


def build_prompt(
    cycle_id: int,
    identity: dict[str, str],
    recent_thoughts: list[Thought],
    context_window: int,
    prompt_context: PromptBuildContext | None = None,
) -> str:
    """Build a single prompt string for thought generation."""
    resolved_context = prompt_context or PromptBuildContext()
    parts = [_timed_prompt_section("system", lambda: _build_system(identity))]
    visible_pending_actions = _visible_pending_actions(resolved_context.running_actions)
    visible_running_actions = _visible_running_actions(resolved_context.running_actions)
    window = recent_thoughts[-context_window * 3:]
    _append_prompt_context_sections(
        parts,
        resolved_context.goal_stack or [],
        resolved_context.emotion,
        resolved_context.sleep_state,
        resolved_context.active_habits or [],
        resolved_context.prefrontal_state,
        resolved_context.recent_reflections or [],
        window,
        resolved_context.long_term_context,
        resolved_context.current_impressions or [],
        resolved_context.note_text,
        resolved_context.perception_cues or [],
    )
    conversations, action_echoes, passive = _split_stimuli(resolved_context.stimuli or [])
    conversation_labels = _conversation_label_map(conversations, resolved_context.recent_conversations)
    _append_prompt_stimulus_sections(
        parts,
        conversations,
        action_echoes,
        resolved_context.recent_action_echoes or [],
        visible_pending_actions,
        visible_running_actions,
        passive,
        resolved_context.recent_conversations or [],
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
        resolved_context.perception_cues or [],
        resolved_context.recent_conversations or [],
        bool(conversations or action_echoes or visible_pending_actions or visible_running_actions),
    )
    if stagnation_warning:
        parts.append(stagnation_warning)
    _append_prompt_section(parts, "next_cycle", lambda: _format_next_cycle(cycle_id))
    return "\n\n".join(parts)


def _append_prompt_context_sections(
    parts: list[str],
    goal_stack: list[str],
    emotion: EmotionSnapshot | None,
    sleep_state: SleepStateSnapshot | None,
    active_habits: list[HabitPromptEntry],
    prefrontal_state: PrefrontalPromptState | None,
    recent_reflections: list[ReflectionPromptEntry],
    window: list[Thought],
    long_term_context: list[str] | None,
    current_impressions: list[str],
    note_text: str,
    perception_cues: list[str],
) -> None:
    if goal_stack or prefrontal_state:
        goals = goal_stack
        executive = prefrontal_state
        _append_prompt_section(parts, "goal_stack", lambda: _format_goal_stack(goals, executive))
    if emotion:
        emotion_state = emotion
        _append_prompt_section(parts, "emotion", lambda: _format_emotion(emotion_state))
    if sleep_state:
        current_sleep_state = sleep_state
        _append_prompt_section(parts, "sleep", lambda: _format_sleep_state(current_sleep_state))
    if active_habits:
        habits = active_habits
        _append_prompt_section(parts, "habits", lambda: _format_habits(habits))
    if recent_reflections:
        reflections = recent_reflections
        _append_prompt_section(parts, "reflections", lambda: _format_recent_reflections(reflections))
    if long_term_context:
        ltm = long_term_context
        _append_prompt_section(parts, "long_term", lambda: _format_long_term(ltm))
    if current_impressions:
        impressions = current_impressions
        _append_prompt_section(parts, "impressions", lambda: _format_impressions(impressions))
    if note_text.strip():
        _append_prompt_section(parts, "note", lambda: _format_note(note_text))
    if perception_cues:
        cues = perception_cues
        _append_prompt_section(parts, "perception_cues", lambda: _format_perception_cues(cues))
    if window:
        _append_prompt_section(parts, "recent_thoughts", lambda: _format_thought_history(window))


def _append_prompt_stimulus_sections(
    parts: list[str],
    conversations: list[Stimulus],
    action_echoes: list[Stimulus],
    recent_action_echoes: list[Stimulus],
    visible_pending_actions: list[ActionRecord],
    visible_running_actions: list[ActionRecord],
    passive: list[Stimulus],
    recent_conversations: list[RecentConversationPrompt],
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
    if recent_conversations:
        convos = recent_conversations
        _append_prompt_section(
            parts,
            "recent_conversations",
            lambda: _format_recent_conversations(convos),
        )
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


def _build_system(identity: dict[str, str]) -> str:
    parts = [SYSTEM_PROMPT.rstrip(), '## “我”是谁']
    for content in identity.values():
        normalized = content.strip()
        if normalized:
            parts.append(normalized)
    return "\n\n".join(parts)


def _format_goal_stack(
    goal_stack: list[str],
    prefrontal_state: PrefrontalPromptState | None,
) -> str:
    lines: list[str] = []
    for goal in goal_stack:
        lines.append(f"- {goal}")
    if prefrontal_state is not None and prefrontal_state["guidance"]:
        lines.append("")
        lines.append("前额叶提醒：")
        for guidance in prefrontal_state["guidance"]:
            lines.append(f"- {guidance}")
    if prefrontal_state is not None and prefrontal_state["inhibition_notes"]:
        lines.append("")
        lines.append("前额叶刚抑制了这些冲动：")
        for note in prefrontal_state["inhibition_notes"]:
            lines.append(f"- {note}")
    return _render_section("当前目标栈", lines, keep_blank_lines=True)


def _format_emotion(emotion: EmotionSnapshot) -> str:
    ranked = sorted(emotion["dimensions"].items(), key=lambda item: item[1], reverse=True)
    lines = [emotion["summary"]]
    lines.append("")
    for name, value in ranked[:5]:
        lines.append(f"- {name}: {value:.2f}")
    return _render_section("当前情绪基调", lines, keep_blank_lines=True)


def _format_sleep_state(sleep_state: SleepStateSnapshot) -> str:
    lines = [sleep_state["summary"]]
    lines.append("")
    lines.append(f"- mode: {sleep_state['mode']}")
    lines.append(f"- energy: {sleep_state['energy']:.1f}/100")
    if sleep_state["last_light_sleep_cycle"] > 0:
        lines.append(f"- last_light_sleep_cycle: C{sleep_state['last_light_sleep_cycle']}")
    if sleep_state["last_deep_sleep_cycle"] > 0:
        lines.append(f"- last_deep_sleep_cycle: C{sleep_state['last_deep_sleep_cycle']}")
    return _render_section("清醒与困意", lines, keep_blank_lines=True)


def _format_habits(habits: list[HabitPromptEntry]) -> str:
    lines = [
        f"- {habit['pattern']} [{habit['category']}, strength={habit['strength']:.2f}]"
        for habit in habits
    ]
    return _render_section("相关习气/倾向性", lines)


def _format_recent_reflections(reflections: list[ReflectionPromptEntry]) -> str:
    lines = [f"- {reflection['content']}" for reflection in reflections]
    return _render_section("最近的反思", lines)


def _format_long_term(memories: list[str]) -> str:
    lines = []
    for mem in memories:
        lines.append(f"- {_compact_prompt_text(mem)}")
    return _render_section("浮上来的记忆", lines)


def _format_impressions(impressions: list[str]) -> str:
    lines = [f"- {_compact_prompt_text(impression)}" for impression in impressions]
    return _render_section("当前人物印象", lines)


def _format_note(note_text: str) -> str:
    return _render_section("我的笔记", [str(note_text).strip()], keep_blank_lines=True)


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
    lines = ["如果我决定回应，需要用 {action:send_message} 真正把话发出去。", ""]
    for conv in conversations:
        lines.append(_format_conversation_line(conv))
    return _render_section("有人对我说话了", lines, keep_blank_lines=True)


def _format_recent_conversations(conversations: list[RecentConversationPrompt]) -> str:
    lines: list[str] = []
    for conversation in conversations:
        last_time = _recent_conversation_local_time(conversation["last_timestamp"])
        lines.append(f'与 {conversation["source_label"]} 的近期对话（最后一条消息时间：{last_time}）：')
        lines.append("")
        summary = str(conversation.get("summary") or "").strip()
        if summary:
            lines.append(f"更早的对话摘要：{summary}")
            lines.append("")
        for message in conversation["messages"]:
            content = _compact_prompt_text(message["content"])
            if content:
                lines.append(f'{message["speaker_name"]}：{content}')
        lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return _render_section("最近的对话", lines, keep_blank_lines=True)


def _format_sensory_stimuli(stimuli: list[Stimulus]) -> str:
    lines = []
    for stimulus in stimuli:
        lines.append(
            f"- {_passive_stimulus_label(stimulus.type)} {_compact_prompt_text(stimulus.content)}"
        )
    return _render_section("此刻我注意到", lines)


def _format_action_echoes(
    recent_stimuli: list[Stimulus],
    current_stimuli: list[Stimulus],
    conversation_labels: dict[str, str],
) -> str:
    lines: list[str] = []
    if recent_stimuli:
        lines.append("最近的行动回音：")
        lines.append("")
        lines.extend(_action_echo_lines(recent_stimuli, conversation_labels))
    if recent_stimuli or current_stimuli:
        if lines:
            lines.append("")
        lines.append("刚刚收到的行动回音：")
        lines.append("")
        if current_stimuli:
            lines.extend(_action_echo_lines(current_stimuli, conversation_labels))
        else:
            lines.append("- 无")
    return _render_section("行动有了回音", lines, keep_blank_lines=True)


def _action_echo_lines(stimuli: list[Stimulus], conversation_labels: dict[str, str]) -> list[str]:
    return [
        f"- {_action_echo_label(stimulus)} {_action_echo_text(stimulus, conversation_labels)}"
        for stimulus in stimuli
    ]


def _format_thought_history(thoughts: list[Thought]) -> str:
    lines = []
    current_cycle = -1
    for t in thoughts:
        if t.cycle_id != current_cycle:
            current_cycle = t.cycle_id
            lines.append(f"--- 第 {t.cycle_id} 轮 ---")
        trigger = f" (← {t.trigger_ref})" if t.trigger_ref else ""
        content = _strip_action_markers(t.content) or t.content
        lines.append(f"[{t.type}-{t.thought_id}] {content}{trigger}")
    return _render_section("最近的念头", lines)


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
    cycle_texts = [
        " ".join(
            normalized
            for thought in cycle_thoughts
            if (normalized := _normalize_stagnation_text(thought.content))
        )
        for cycle_thoughts in recent_cycles
    ]
    if any(not text for text in cycle_texts):
        return ""
    similar_pairs = 0
    total_pairs = 0
    for i in range(len(cycle_texts)):
        for j in range(i + 1, len(cycle_texts)):
            total_pairs += 1
            if _text_similarity(cycle_texts[i], cycle_texts[j]) >= STAGNATION_SIMILARITY_THRESHOLD:
                similar_pairs += 1
    if total_pairs > 0 and similar_pairs == total_pairs:
        return _stagnation_warning(cycle_texts, available_sources, has_foreground)
    return ""


def _group_recent_cycles(thoughts: list[Thought], n: int) -> list[list[Thought]]:
    cycles: dict[int, list[Thought]] = {}
    for t in thoughts:
        cycles.setdefault(t.cycle_id, []).append(t)
    sorted_cycle_ids = sorted(cycles.keys())[-n:]
    return [cycles[cid] for cid in sorted_cycle_ids]


def _stagnation_sources(
    long_term_context: list[str] | None,
    note_text: str,
    recent_action_echoes: list[Stimulus],
    action_echoes: list[Stimulus],
    pending_actions: list[ActionRecord],
    running_actions: list[ActionRecord],
    passive: list[Stimulus],
    perception_cues: list[str],
    recent_conversations: list[RecentConversationPrompt],
) -> list[str]:
    sources: list[str] = []
    if long_term_context:
        sources.append("浮上来的记忆")
    if note_text.strip():
        sources.append("我的笔记")
    if recent_action_echoes or action_echoes:
        sources.append("行动有了回音")
    if pending_actions:
        sources.append("正在受理中的行动")
    if running_actions:
        sources.append("我已经发起、正在等回音的事")
    if passive:
        sources.append("此刻我注意到")
    if perception_cues:
        sources.append("好像有一阵子没有……")
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
        repeated_text = f"最近反复出现的意象：{', '.join(repeated_terms)}。"
    else:
        repeated_text = "最近几轮一直在复述同一组意象和情绪。"
    if available_sources:
        source_text = "、".join(available_sources)
    else:
        source_text = "一个新的具体问题、记忆、感知或行动"
    if has_foreground:
        return (
            "⚠ 最近 3 轮念头进入死循环。"
            f"{repeated_text}"
            "不要再机械改写同一句话或同一组意象。"
            f"这一轮至少一个念头必须明确引入新的源：{source_text}。"
            "如果眼前有人在说话，最多只让一个念头承接当前对话，其余念头不要继续复述。"
        )
    return (
        "⚠ 最近 3 轮念头进入死循环。"
        f"{repeated_text}"
        f"这一轮至少一个念头必须明确引入新的源：{source_text}。"
        "不要三个念头继续围着同一组意象改写。"
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
        if candidate in STAGNATION_TERM_STOPWORDS:
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
    while len(trimmed) >= 3 and trimmed[:1] in {"和", "与"}:
        trimmed = trimmed[1:]
    return trimmed


def _text_similarity(a: str, b: str) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    bigrams_a = {a[i:i + 2] for i in range(len(a) - 1)}
    bigrams_b = {b[i:i + 2] for i in range(len(b) - 1)}
    intersection = len(bigrams_a & bigrams_b)
    union = len(bigrams_a | bigrams_b)
    return intersection / union if union > 0 else 0.0


def _format_running_actions(actions: list[ActionRecord], conversation_labels: dict[str, str]) -> str:
    lines = []
    for action in actions:
        lines.append(
            f"- [{action.type}/{action.status}] {_running_action_summary(action, conversation_labels)}"
        )
    return _render_section("我已经发起、正在等回音的事", lines)


def _format_pending_actions(actions: list[ActionRecord], conversation_labels: dict[str, str]) -> str:
    lines = []
    for action in actions:
        lines.append(
            f"- [{action.type}/{action.status}] {_pending_action_summary(action, conversation_labels)}"
        )
    return _render_section("正在受理中的行动", lines)


def _visible_pending_actions(actions: list[ActionRecord] | None) -> list[ActionRecord]:
    return [
        action for action in (actions or []) if action.status in PENDING_ACTION_VISIBLE_STATUSES
    ]


def _visible_running_actions(actions: list[ActionRecord] | None) -> list[ActionRecord]:
    return [
        action for action in (actions or []) if action.status in RUNNING_ACTION_VISIBLE_STATUSES
    ]


def _format_perception_cues(cues: list[str]) -> str:
    return _render_section("好像有一阵子没有……", [f"- {cue}" for cue in cues])


def _format_next_cycle(cycle_id: int) -> str:
    return f"## 接下来的念头\n\n--- 第 {cycle_id} 轮 ---"


def _render_section(title: str, lines: list[str], *, keep_blank_lines: bool = False) -> str:
    if keep_blank_lines:
        body = "\n".join(lines)
    else:
        body = "\n".join(line for line in lines if line)
    return f"## {title}\n\n{body}"


def _passive_stimulus_label(stimulus_type: str) -> str:
    return PASSIVE_STIMULUS_LABELS.get(stimulus_type, "[感知]")


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
    return f"{_conversation_prefix(stimulus)} 说：{content}"


def _format_conversation_block(message: JsonValue) -> str:
    if not isinstance(message, dict):
        return _compact_prompt_text(str(message or ""))
    content = _compact_prompt_text(str(message.get("content") or ""))
    prefix = _conversation_dict_prefix(message)
    if not prefix:
        return content
    if not content:
        return prefix
    return f"{prefix} 说：\n{content}"


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
        quoted_owner = "引用了我之前说的"
    else:
        quoted_owner = "引用了自己之前说的"
    return f'{quoted_owner} [msg:{reply_to_message_id}]：“{reply_preview}”'


def _conversation_speaker(stimulus: Stimulus) -> str:
    return _person_label(stimulus.source, stimulus.metadata)


def _conversation_reply_context(stimulus: Stimulus) -> str:
    reply_to_message_id = stimulus.metadata.get("reply_to_message_id")
    reply_preview = _compact_prompt_text(str(stimulus.metadata.get("reply_to_preview") or ""))
    if not reply_to_message_id or not reply_preview:
        return ""
    if bool(stimulus.metadata.get("reply_to_from_self")):
        quoted_owner = "引用了我之前说的"
    else:
        quoted_owner = "引用了自己之前说的"
    return f'{quoted_owner} [msg:{reply_to_message_id}]：“{reply_preview}”'


def _action_echo_label(stimulus: Stimulus) -> str:
    action_type = str(stimulus.metadata.get("action_type") or "").strip()
    if action_type:
        return ACTION_ECHO_LABELS.get(action_type, UNKNOWN_ACTION_ECHO_LABEL)
    return UNKNOWN_ACTION_ECHO_LABEL


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
        return f"已受理，等待确认：{summary}"
    if action.retry_after is not None:
        return f"已受理，等待恢复后重试：{summary}"
    return f"已受理，等待执行：{summary}"


def _running_send_message_summary(action: ActionRecord, conversation_labels: dict[str, str]) -> str:
    target = _known_target_label(
        str(
            action.request.get("target_source")
            or action.request.get("target_entity")
            or "当前 Telegram 对话"
        ).strip(),
        conversation_labels,
    )
    message = _running_message_excerpt(str(action.request.get("message_text") or ""))
    if message:
        return f"给 {target} 发送消息：“{message}”"
    task_summary = _running_action_task_summary(action)
    if task_summary:
        return task_summary
    return f"给 {target} 发送消息"


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
        return f'已成功发送给 {target}：“{message}”'
    if succeeded and target:
        return f"已成功发送给 {target}"
    if target and message:
        return f'发送给 {target} 失败：“{message}” （{summary}）'
    if message:
        return f'发送失败：“{message}” （{summary}）'
    if target:
        return f"发送给 {target} 失败（{summary}）"
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
