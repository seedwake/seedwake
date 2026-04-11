"""Parse semi-structured thought output from LLM into Thought objects."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.common_types import RawActionRequest

# Canonical internal thought type keys (language-independent)
THINKING = "thinking"
INTENTION = "intention"
REACTION = "reaction"
REFLECTION = "reflection"

TRIGGER_PATTERN = re.compile(
    r"^(?P<content>.*?)(?:\s*\(←\s*(?P<trigger>[^)]+)\))?\s*$", re.DOTALL
)
INLINE_CODE_SPAN_PATTERN = re.compile(r"`[^`]*`", re.DOTALL)
ACTION_PATTERN = re.compile(
    r"\{action:(\w+)(?:,\s*(.+?))?\s*\}", re.DOTALL
)

# Lazy-initialized patterns (built on first use from i18n labels)
_header_pattern: re.Pattern | None = None
_skipped_pattern: re.Pattern | None = None
_label_to_canonical: dict[str, str] = {}
_pattern_labels: tuple[str, str, str, str] | None = None


def _ensure_patterns() -> None:
    global _header_pattern, _skipped_pattern, _label_to_canonical, _pattern_labels
    from core.i18n import thought_types
    labels = thought_types()
    if (
        _header_pattern is not None
        and _skipped_pattern is not None
        and _pattern_labels == labels
    ):
        return
    thinking_label, intention_label, reaction_label, reflection_label = labels
    _pattern_labels = labels
    _label_to_canonical = {
        thinking_label: THINKING,
        intention_label: INTENTION,
        reaction_label: REACTION,
        reflection_label: REFLECTION,
    }
    escaped_types = "|".join(
        re.escape(label) for label in [thinking_label, intention_label, reaction_label]
    )
    _header_pattern = re.compile(
        rf"^\[(?P<type>{escaped_types})(?:-C\d+-\d+)?]\s*(?P<content>.*)$",
    )
    _skipped_pattern = re.compile(
        rf"^\[{re.escape(reflection_label)}(?:-C\d+-\d+)?]"
    )


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
    _ensure_patterns()
    assert _header_pattern is not None
    assert _skipped_pattern is not None
    thoughts = []
    current_type: str | None = None
    current_lines: list[str] = []

    skipping = False
    for raw_line in raw_output.splitlines():
        line = raw_line.strip()

        if _skipped_pattern.match(line):
            skipping = True
            continue

        header_match = _header_pattern.match(line)
        if header_match:
            skipping = False
            if current_type is not None:
                thoughts.append(
                    _build_thought(cycle_id, len(thoughts) + 1, current_type, current_lines)
                )
            # Map localized label to canonical key
            localized_type = header_match.group("type")
            current_type = _label_to_canonical.get(localized_type, localized_type)
            current_lines = [header_match.group("content").strip()]
            continue

        if skipping:
            continue

        if current_type is not None:
            current_lines.append(line)

    if current_type is not None:
        thoughts.append(
            _build_thought(cycle_id, len(thoughts) + 1, current_type, current_lines)
        )

    return thoughts[:3]


def fallback_thought(raw_output: str, cycle_id: int) -> Thought:
    """Wrap unparseable raw output as a single thought."""
    from core.i18n import t
    content = _clip_text(raw_output) or t("thought.fallback_empty")
    return _make_thought(cycle_id, 1, THINKING, content)


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
