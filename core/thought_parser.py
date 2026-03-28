"""Parse semi-structured thought output from LLM into Thought objects."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.types import RawActionRequest

THOUGHT_HEADER_PATTERN = re.compile(
    r"^\[(?P<type>思考|意图|反应)(?:-C\d+-\d+)?]\s*(?P<content>.*)$",
)
# Phase 4 type — recognize to skip, not to parse
SKIPPED_HEADER_PATTERN = re.compile(r"^\[反思(?:-C\d+-\d+)?]")
TRIGGER_PATTERN = re.compile(r"^(?P<content>.*?)(?:\s*\(←\s*(?P<trigger>[^)]+)\))?\s*$", re.DOTALL)
ACTION_PATTERN = re.compile(r"\{action:(\w+),\s*(.+?)}")


@dataclass
class Thought:
    thought_id: str
    cycle_id: int
    index: int
    type: str
    content: str
    trigger_ref: str | None = None
    action_request: RawActionRequest | None = None
    attention_weight: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def parse_thoughts(raw_output: str, cycle_id: int) -> list[Thought]:
    """Extract thoughts from LLM output."""
    thoughts = []
    current_type: str | None = None
    current_lines: list[str] = []

    skipping = False
    for raw_line in raw_output.splitlines():
        line = raw_line.strip()

        if SKIPPED_HEADER_PATTERN.match(line):
            skipping = True
            continue

        header_match = THOUGHT_HEADER_PATTERN.match(line)
        if header_match:
            skipping = False
            if current_type is not None:
                thoughts.append(_build_thought(cycle_id, len(thoughts) + 1, current_type, current_lines))
            current_type = header_match.group("type")
            current_lines = [header_match.group("content").strip()]
            continue

        if skipping:
            continue

        if current_type is not None:
            current_lines.append(line)

    if current_type is not None:
        thoughts.append(_build_thought(cycle_id, len(thoughts) + 1, current_type, current_lines))

    return thoughts[:3]


def fallback_thought(raw_output: str, cycle_id: int) -> Thought:
    """Wrap unparseable raw output as a single thought."""
    return _make_thought(cycle_id, 1, "思考", _clip_text(raw_output))


def _parse_action(content: str) -> RawActionRequest | None:
    m = ACTION_PATTERN.search(content)
    if not m:
        return None
    return {"type": m.group(1), "params": m.group(2)}


def _build_thought(
    cycle_id: int,
    index: int,
    thought_type: str,
    content_lines: list[str],
) -> Thought:
    content, trigger_ref = _split_trigger("\n".join(content_lines).strip())
    return _make_thought(cycle_id, index, thought_type, content, trigger_ref)


def _make_thought(
    cycle_id: int,
    index: int,
    thought_type: str,
    content: str,
    trigger_ref: str | None = None,
) -> Thought:
    return Thought(
        thought_id=f"C{cycle_id}-{index}",
        cycle_id=cycle_id,
        index=index,
        type=thought_type,
        content=content,
        trigger_ref=trigger_ref,
        action_request=_parse_action(content),
    )


def _split_trigger(content: str) -> tuple[str, str | None]:
    match = TRIGGER_PATTERN.match(content)
    if not match:
        return content, None
    clean_content = match.group("content").strip()
    trigger_ref = match.group("trigger")
    return clean_content, trigger_ref


def _clip_text(raw_output: str, limit: int = 500) -> str:
    return raw_output.strip()[:limit]
