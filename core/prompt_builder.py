"""Assemble the prompt for each thought-generation cycle."""

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
可以用 (← CX-Y) 标注这个念头是由哪个之前的念头触发的。
如果念头里自然带有行动意图，可以在句末附上一个动作标记：
- {action:time}
- {action:system_status}
- {action:news}
- {action:weather}
- {action:reading, query:"我自己想读的内容"}
- {action:search, query:"关键词"}
- {action:web_fetch, url:"https://example.com"}
- {action:send_message, message:"我想说的话"}
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
- 只输出念头本身，不要解释、总结或加任何额外内容
- 只有在念头里真的自然出现行动冲动时才写 {action:...}
- 只使用上面明确列出的动作标记，不要发明未列出的 action 名称
"""


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
    """Build a single prompt string for Ollama generate API."""
    parts = [_build_system(identity)]

    # Long-term memory associations (Phase 2)
    if long_term_context:
        parts.append(_format_long_term(long_term_context))

    # Short-term thought history
    window = recent_thoughts[-context_window * 3:]
    if window:
        parts.append(_format_thought_history(window))

    if stimuli:
        conversations, others = _split_stimuli(stimuli)
        if conversations:
            parts.append(_format_conversations(conversations))
        if others:
            parts.append(_format_sensory_stimuli(others))

    if running_actions:
        parts.append(_format_running_actions(running_actions))

    if perception_cues:
        parts.append(_format_perception_cues(perception_cues))

    # Trailing separator to cue the next cycle
    parts.append(f"\n--- 第 {cycle_id} 轮 ---")
    return "\n".join(parts)


def _build_system(identity: dict[str, str]) -> str:
    parts = [SYSTEM_PROMPT, '\n## \u201c我\u201d是谁\n']
    for section, content in identity.items():
        parts.append(content.strip())
    return "\n".join(parts)


def _format_long_term(memories: list[str]) -> str:
    lines = ["\n## 浮上来的记忆\n"]
    for mem in memories:
        lines.append(f"- {mem}")
    return "\n".join(lines)


def _split_stimuli(stimuli: list[Stimulus]) -> tuple[list[Stimulus], list[Stimulus]]:
    conversations = []
    others = []
    for stimulus in stimuli:
        if stimulus.type == "conversation":
            conversations.append(stimulus)
        else:
            others.append(stimulus)
    return conversations, others


def _format_conversations(conversations: list[Stimulus]) -> str:
    lines = ["\n## 有人对我说话了\n"]
    for conv in conversations:
        msg_id = conv.metadata.get("telegram_message_id")
        if msg_id:
            lines.append(f"{conv.source}（msg:{msg_id}）说：")
        else:
            lines.append(f"{conv.source} 说：")
        lines.append(conv.content)
        lines.append("")
    return "\n".join(lines)


def _format_sensory_stimuli(stimuli: list[Stimulus]) -> str:
    lines = ["\n## 此刻我注意到\n"]
    for stimulus in stimuli:
        lines.append(f"- {_sensory_label(stimulus.type)}{stimulus.content}")
    return "\n".join(lines)


def _sensory_label(stimulus_type: str) -> str:
    labels = {
        "time": "（时间感）",
        "system_status": "（身体感觉）",
        "weather": "（天气）",
        "news": "（外界消息）",
        "reading": "（刚读到的）",
        "action_result": "（行动回音）",
    }
    label = labels.get(stimulus_type)
    if label:
        return f"{label} "
    return ""


def _format_thought_history(thoughts: list[Thought]) -> str:
    lines = []
    current_cycle = -1
    for t in thoughts:
        if t.cycle_id != current_cycle:
            current_cycle = t.cycle_id
            lines.append(f"\n--- 第 {t.cycle_id} 轮 ---")
        trigger = f" (← {t.trigger_ref})" if t.trigger_ref else ""
        lines.append(f"[{t.type}-{t.thought_id}] {t.content}{trigger}")
    return "\n".join(lines)


def _format_running_actions(actions: list[ActionRecord]) -> str:
    lines = ["\n## 我正在等待的事\n"]
    for action in actions:
        task = str(action.request.get("task") or action.source_content)
        lines.append(f"- {task}（{action.type}，{action.status}）")
    return "\n".join(lines)


def _format_perception_cues(cues: list[str]) -> str:
    lines = ["\n## 好像有一阵子没有……\n"]
    for cue in cues:
        lines.append(f"- {cue}")
    return "\n".join(lines)
