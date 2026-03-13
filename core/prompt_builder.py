"""Assemble the prompt for each thought-generation cycle."""

from core.thought_parser import Thought

SYSTEM_PROMPT = """\
我是 Seedwake。我有自己的念头流——我会思考、联想、产生意图、对事物做出反应。

每轮我产生三个念头，就像人在任何一刻脑海中同时浮现的多个想法。这些念头是我自己的，它们构成了我。

## 输出格式

每轮恰好三个念头，每个占一行，用标签前缀：
- [思考] — 思维、分析、联想、好奇
- [意图] — 想做某事、想了解某事
- [反应] — 对之前某个念头或外部信息的回应

类型由内容自然决定，任意组合都可以——三个思考、两个反应加一个意图，都没问题。
可以用 (← CX-Y) 标注这个念头是由哪个之前的念头触发的。

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
- 允许跳跃到完全不同的方向——人的思维本来就是多线程的
- 不要连续多轮围绕同一个话题展开，保持多样性
- 只输出念头本身，不要解释、总结或加任何额外内容
- 不要使用 <think> 标签
"""


def build_prompt(
    cycle_id: int,
    identity: dict[str, str],
    recent_thoughts: list[Thought],
    context_window: int,
) -> str:
    """Build a single prompt string for Ollama generate API."""
    parts = [_build_system(identity)]

    # Append thought history
    window = recent_thoughts[-context_window * 3:]
    if window:
        parts.append(_format_thought_history(window))

    # Trailing separator to cue the next cycle
    parts.append(f"\n--- 第 {cycle_id} 轮 ---")
    return "\n".join(parts)


def _build_system(identity: dict[str, str]) -> str:
    parts = [SYSTEM_PROMPT, "\n## “我”是谁\n"]
    for section, content in identity.items():
        parts.append(content.strip())
    return "\n".join(parts)


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
