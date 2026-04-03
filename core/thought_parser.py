"""Parse semi-structured thought output from LLM into Thought objects."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.common_types import RawActionRequest

THOUGHT_HEADER_PATTERN = re.compile(
    r"^\[(?P<type>思考|意图|反应)(?:-C\d+-\d+)?]\s*(?P<content>.*)$",
)
# Phase 4 type — recognize to skip, not to parse
SKIPPED_HEADER_PATTERN = re.compile(r"^\[反思(?:-C\d+-\d+)?]")
TRIGGER_PATTERN = re.compile(r"^(?P<content>.*?)(?:\s*\(←\s*(?P<trigger>[^)]+)\))?\s*$", re.DOTALL)
INLINE_CODE_SPAN_PATTERN = re.compile(r"`[^`]*`", re.DOTALL)
ACTION_PATTERN = re.compile(r"\{action:(\w+)(?:,\s*(.+?))?\s*\}", re.DOTALL)


@dataclass
class Thought:
    thought_id: str
    cycle_id: int
    index: int
    type: str
    content: str
    trigger_ref: str | None = None
    action_request: RawActionRequest | None = None
    additional_action_requests: list[RawActionRequest] = field(default_factory=list)
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


def thought_action_requests(thought: Thought) -> list[RawActionRequest]:
    action_requests: list[RawActionRequest] = []
    if thought.action_request is not None:
        action_requests.append(thought.action_request)
    action_requests.extend(thought.additional_action_requests)
    return action_requests


def strip_action_markers(content: str) -> str:
    return ACTION_PATTERN.sub("", content).strip()


def _parse_actions(content: str) -> list[RawActionRequest]:
    sanitized = INLINE_CODE_SPAN_PATTERN.sub("", content)
    return [
        {"type": match.group(1), "params": match.group(2) or ""}
        for match in ACTION_PATTERN.finditer(sanitized)
    ]


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
    action_requests = _parse_actions(content)
    return Thought(
        thought_id=f"C{cycle_id}-{index}",
        cycle_id=cycle_id,
        index=index,
        type=thought_type,
        content=content,
        trigger_ref=trigger_ref,
        action_request=action_requests[0] if action_requests else None,
        additional_action_requests=action_requests[1:],
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
