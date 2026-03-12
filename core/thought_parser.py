"""Parse semi-structured thought output from LLM into Thought objects."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


# Matches both [思考-C1-1] and [思考] (model may omit the ID)
THOUGHT_PATTERN = re.compile(
    r"\[(?P<type>思考|意图|反应)(?:-C\d+-\d+)?\]\s*(?P<content>.+?)(?:\s*\(←\s*(?P<trigger>[^)]+)\))?\s*$",
    re.MULTILINE,
)

ACTION_PATTERN = re.compile(r"\{action:(\w+),\s*(.+?)\}")


@dataclass
class Thought:
    thought_id: str
    cycle_id: int
    index: int
    type: str
    content: str
    trigger_ref: str | None = None
    action_request: dict | None = None
    attention_weight: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def parse_thoughts(raw_output: str, cycle_id: int) -> list[Thought]:
    """Extract thoughts from LLM output. Returns up to 3 thoughts."""
    matches = list(THOUGHT_PATTERN.finditer(raw_output))
    thoughts = []
    for i, m in enumerate(matches[:3], start=1):
        content = m.group("content").strip()
        action = _parse_action(content)
        thoughts.append(Thought(
            thought_id=f"C{cycle_id}-{i}",
            cycle_id=cycle_id,
            index=i,
            type=m.group("type"),
            content=content,
            trigger_ref=m.group("trigger"),
            action_request=action,
        ))
    return thoughts


def _parse_action(content: str) -> dict | None:
    m = ACTION_PATTERN.search(content)
    if not m:
        return None
    return {"type": m.group(1), "params": m.group(2)}
