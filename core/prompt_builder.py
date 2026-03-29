"""Assemble the prompt for each thought-generation cycle."""

import re

from core.action import ActionRecord
from core.stimulus import Stimulus
from core.thought_parser import Thought

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
- {action:weather, location:"某个位置"}
- {action:reading}
- {action:reading, query:"我自己想读的内容"}
- {action:search, query:"关键词"}
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


def build_prompt(
    cycle_id: int,
    identity: dict[str, str],
    recent_thoughts: list[Thought],
    context_window: int,
    long_term_context: list[str] | None = None,
    stimuli: list[Stimulus] | None = None,
    running_actions: list[ActionRecord] | None = None,
    perception_cues: list[str] | None = None,
) -> str:
    """Build a single prompt string for thought generation."""
    parts = [_build_system(identity)]

    window = recent_thoughts[-context_window * 3:]
    if window:
        parts.append(_format_thought_history(window))
    if long_term_context:
        parts.append(_format_long_term(long_term_context))
    if perception_cues:
        parts.append(_format_perception_cues(perception_cues))
    if running_actions:
        parts.append(_format_running_actions(running_actions))

    if stimuli:
        conversations, action_echoes, passive = _split_stimuli(stimuli)
        if passive:
            parts.append(_format_sensory_stimuli(passive))
        if action_echoes:
            parts.append(_format_action_echoes(action_echoes))
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
        content = _compact_prompt_text(conv.content)
        msg_id = conv.metadata.get("telegram_message_id")
        if msg_id:
            lines.append(f"{conv.source} [msg:{msg_id}] 说：{content}")
            continue
        lines.append(f"{conv.source} 说：{content}")
    return _render_section("有人对我说话了", lines, keep_blank_lines=True)


def _format_sensory_stimuli(stimuli: list[Stimulus]) -> str:
    lines = []
    for stimulus in stimuli:
        lines.append(
            f"- {_passive_stimulus_label(stimulus.type)} {_compact_prompt_text(stimulus.content)}"
        )
    return _render_section("此刻我注意到", lines)


def _format_action_echoes(stimuli: list[Stimulus]) -> str:
    lines = []
    for stimulus in stimuli:
        lines.append(
            f"- {_action_echo_label(stimulus)} {_compact_prompt_text(stimulus.content)}"
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


def _format_running_actions(actions: list[ActionRecord]) -> str:
    lines = []
    for action in actions:
        lines.append(f"- {_running_action_summary(action)} [{action.type}/{action.status}]")
    return _render_section("我已经发起、正在等回音的事", lines)


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


def _running_action_summary(action: ActionRecord) -> str:
    summary = action.source_content.strip() or str(action.request.get("reason") or "").strip() or action.type
    return _compact_prompt_text(_strip_action_marker(summary))


def _strip_action_marker(content: str) -> str:
    return ACTION_MARKER_SUFFIX_PATTERN.sub("", content).strip()


def _compact_prompt_text(text: str) -> str:
    return " ".join(str(text).split())
