"""Assemble the prompt for each thought-generation cycle."""

from datetime import datetime
import re

from core.action import ActionRecord
from core.stimulus import Stimulus
from core.thought_parser import Thought
from core.types import JsonObject, RecentConversationPrompt

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
历史里出现的 [思考-CX-Y]、[意图-CX-Y]、[反应-CX-Y] 是系统记录用编号，方便回看和引用；我生成新念头时不用自己写编号。
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
- {action:file_modify, path:"文件路径", instruction:"修改要求"}
- {action:system_change, instruction:"我想进行的系统变更"}

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
- 有人对我说话或有行动结果回来时，至少一个念头应该回应它
- 如果我心里已经是在回应对方、安抚对方、接住对方、回答对方，或者我明确想把一句话递给对方，这种回应必须外化成 {action:send_message, ...}，不能只停留在“我想回应/我想接住/我想靠近”的内在意图
- 当 conversation 里有人直接提问、催我回复、说自己在等待，或明确要求我和他说话时，“回应它”通常意味着优先发出一条 {action:send_message, ...}，而不是继续只在内部流动
- 当 conversation 和时间感/身体感觉同时出现时，对话是前景，时间感和身体感觉只是背景；不要让这些背景感受盖过对眼前这个人的回应
- 只输出念头本身，不要解释、总结或加任何额外内容
- 只有在念头里真的自然出现行动冲动时才写 {action:...}
- 只使用上面明确列出的动作标记，不要发明未列出的 action 名称
- 回复别人时，如果想针对某条具体消息，用 reply_to 带上对方的 msg id；不带则发普通消息
"""

ACTION_MARKER_SUFFIX_PATTERN = re.compile(r"\s*\{action:[^}]+\}\s*$")
CONVERSATION_MERGE_SEPARATOR = " / "
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
    "file_modify": "[文件修改]",
    "system_change": "[系统变更]",
}
UNKNOWN_ACTION_ECHO_LABEL = "[结果]"
RUNNING_ACTION_VISIBLE_STATUSES = {"pending", "running"}


def build_prompt(
    cycle_id: int,
    identity: dict[str, str],
    recent_thoughts: list[Thought],
    context_window: int,
    long_term_context: list[str] | None = None,
    stimuli: list[Stimulus] | None = None,
    running_actions: list[ActionRecord] | None = None,
    perception_cues: list[str] | None = None,
    recent_conversations: list[RecentConversationPrompt] | None = None,
) -> str:
    """Build a single prompt string for thought generation."""
    parts = [_build_system(identity)]
    visible_running_actions = _visible_running_actions(running_actions)
    conversations: list[Stimulus] = []
    action_echoes: list[Stimulus] = []
    passive: list[Stimulus] = []

    window = recent_thoughts[-context_window * 3:]
    if window:
        parts.append(_format_thought_history(window))
    if long_term_context:
        parts.append(_format_long_term(long_term_context))
    if perception_cues:
        parts.append(_format_perception_cues(perception_cues))

    if stimuli:
        conversations, action_echoes, passive = _split_stimuli(stimuli)
    conversation_labels = _conversation_label_map(conversations, recent_conversations)
    if action_echoes:
        parts.append(_format_action_echoes(action_echoes, conversation_labels))
    if visible_running_actions:
        parts.append(_format_running_actions(visible_running_actions, conversation_labels))
    if passive:
        parts.append(_format_sensory_stimuli(passive))
    if recent_conversations:
        parts.append(_format_recent_conversations(recent_conversations))
    if conversations:
        parts.append(_format_conversations(conversations))
    parts.append(_format_next_cycle(cycle_id))
    return "\n\n".join(parts)


def _build_system(identity: dict[str, str]) -> str:
    parts = [SYSTEM_PROMPT.rstrip(), '## “我”是谁']
    for content in identity.values():
        normalized = content.strip()
        if normalized:
            parts.append(normalized)
    return "\n\n".join(parts)


def _format_long_term(memories: list[str]) -> str:
    lines = []
    for mem in memories:
        lines.append(f"- {_compact_prompt_text(mem)}")
    return _render_section("浮上来的记忆", lines)


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
        lines.append(f'与 {conversation["source_label"]} 的近期对话：')
        lines.append("")
        summary = str(conversation.get("summary") or "").strip()
        if summary:
            lines.append(f"- 对话历史摘要：{summary}")
        lines.append(
            f'- 最后一条消息时间：{_recent_conversation_local_time(conversation["last_timestamp"])}'
        )
        for message in conversation["messages"]:
            content = _compact_prompt_text(message["content"])
            if content:
                lines.append(f'- {message["speaker_label"]}：{content}')
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


def _format_action_echoes(stimuli: list[Stimulus], conversation_labels: dict[str, str]) -> str:
    lines = []
    for stimulus in stimuli:
        lines.append(
            f"- {_action_echo_label(stimulus)} {_action_echo_text(stimulus, conversation_labels)}"
        )
    return _render_section("行动有了回音", lines)


def _format_thought_history(thoughts: list[Thought]) -> str:
    lines = []
    current_cycle = -1
    for t in thoughts:
        if t.cycle_id != current_cycle:
            current_cycle = t.cycle_id
            lines.append(f"--- 第 {t.cycle_id} 轮 ---")
        trigger = f" (← {t.trigger_ref})" if t.trigger_ref else ""
        lines.append(f"[{t.type}-{t.thought_id}] {t.content}{trigger}")
    return _render_section("最近的念头", lines)


def _format_running_actions(actions: list[ActionRecord], conversation_labels: dict[str, str]) -> str:
    lines = []
    for action in actions:
        lines.append(
            f"- [{action.type}/{action.status}] {_running_action_summary(action, conversation_labels)}"
        )
    return _render_section("我已经发起、正在等回音的事", lines)


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


def _format_conversation_block(message: object) -> str:
    if not isinstance(message, dict):
        return _compact_prompt_text(str(message or ""))
    content = _compact_prompt_text(str(message.get("content") or ""))
    prefix = _conversation_dict_prefix(message)
    if not prefix:
        return content
    if not content:
        return prefix
    return f"{prefix} 说：\n{content}"


def _conversation_dict_prefix(message: dict) -> str:
    parts = [_conversation_dict_speaker(message)]
    message_id = message.get("telegram_message_id")
    if message_id:
        parts.append(f"[msg:{message_id}]")
    reply_context = _conversation_dict_reply_context(message)
    if reply_context:
        parts.append(reply_context)
    return " ".join(str(part).strip() for part in parts if str(part).strip())


def _conversation_dict_speaker(message: dict) -> str:
    source = str(message.get("source") or "").strip()
    metadata = _conversation_metadata_from_dict(message)
    return _person_label(source, metadata)


def _conversation_dict_reply_context(message: dict) -> str:
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


def _compact_prompt_text(text: str) -> str:
    return " ".join(str(text).split())


def _action_echo_text(stimulus: Stimulus, conversation_labels: dict[str, str]) -> str:
    action_type = str(stimulus.metadata.get("action_type") or "").strip()
    if action_type != "send_message":
        return _compact_prompt_text(stimulus.content)
    result = stimulus.metadata.get("result")
    if not isinstance(result, dict):
        return _compact_prompt_text(stimulus.content)
    data = result.get("data")
    if not isinstance(data, dict):
        return _compact_prompt_text(stimulus.content)
    target = _known_target_label(str(data.get("source") or "").strip(), conversation_labels)
    message = _running_message_excerpt(str(data.get("message") or ""))
    if target and message:
        return f'已成功发送给 {target}：“{message}”'
    if target:
        return f"已成功发送给 {target}"
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
