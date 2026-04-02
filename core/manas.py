"""Manas (self-continuity) tracking for Phase 4."""

import hashlib
import json
import logging
import time
from dataclasses import dataclass

import redis as redis_lib

from core.embedding import embed_texts
from core.model_client import MODEL_CLIENT_EXCEPTIONS, ModelClient
from core.thought_parser import Thought, strip_action_markers
from core.types import ManasPromptState, elapsed_ms

MANAS_STATE_KEY = "seedwake:manas_state"
MANAS_REDIS_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
    OSError,
)
SELF_CONTINUITY_ANCHOR = "我从上一刻延续到这一刻，此刻仍在作为正在经历者而存在。"
EXTERNALIZED_SELF_ANCHOR = "这个系统只是被外部观察的对象，与当下经历者脱开了。"
logger = logging.getLogger(__name__)


@dataclass
class _ManasState:
    self_coherence_score: float = 1.0
    last_stable_cycle: int = 0
    consecutive_disruptions: int = 0
    session_start_cycle: int = 0
    session_context: str = ""
    identity_hash: str = ""
    session_context_pending: bool = False
    identity_notice_pending: bool = False


class ManasManager:
    def __init__(
        self,
        redis_client: redis_lib.Redis | None,
        *,
        warning_threshold: int,
        reflection_threshold: int,
        stable_window: int,
    ) -> None:
        self._redis = redis_client
        self._warning_threshold = max(1, warning_threshold)
        self._reflection_threshold = max(self._warning_threshold, reflection_threshold)
        self._stable_window = max(3, stable_window)
        self._state = _ManasState()
        self._state_dirty = False
        self._restore_state(notify_restart=True)

    def attach_redis(self, redis_client: redis_lib.Redis | None) -> bool:
        self._redis = redis_client
        if not self._state_dirty:
            self._restore_state(notify_restart=False)
        self._sync_state()
        return self._redis is not None

    def current_prompt_state(
        self,
        *,
        cycle_id: int,
        identity: dict[str, str],
    ) -> ManasPromptState:
        self._prepare_identity_notice(identity)
        if self._state.session_start_cycle <= 0:
            self._state.session_start_cycle = cycle_id
            self._state_dirty = True
            self._sync_state()
        return _prompt_state(
            self._state,
            warning_threshold=self._warning_threshold,
            reflection_threshold=self._reflection_threshold,
        )

    def evaluate_cycle(
        self,
        *,
        cycle_id: int,
        thoughts: list[Thought],
        recent_thoughts: list[Thought],
        identity: dict[str, str],
        embedding_client: ModelClient,
        embedding_model: str,
        stm_available: bool,
        ltm_available: bool,
    ) -> ManasPromptState:
        score = _self_coherence_score(
            thoughts=thoughts,
            recent_thoughts=recent_thoughts,
            identity=identity,
            session_context=self._state.session_context,
            stable_window=self._stable_window,
            embedding_client=embedding_client,
            embedding_model=embedding_model,
        )
        if not stm_available:
            score *= 0.90
        if not ltm_available:
            score *= 0.94
        score = round(min(1.0, max(0.0, score)), 4)
        self._state.self_coherence_score = score
        if score < 0.48:
            self._state.consecutive_disruptions += 1
        else:
            self._state.consecutive_disruptions = 0
            self._state.last_stable_cycle = cycle_id
        self._state.session_context_pending = False
        self._state.identity_notice_pending = False
        self._state_dirty = True
        self._sync_state()
        return _prompt_state(
            self._state,
            warning_threshold=self._warning_threshold,
            reflection_threshold=self._reflection_threshold,
        )

    def note_sleep_transition(self, context: str) -> None:
        compact = " ".join(context.split()).strip()
        if not compact:
            return
        self._state.session_context = compact
        self._state.session_context_pending = True
        self._state_dirty = True
        self._sync_state()

    def note_restart_restoration(self, *, redis_restored: bool, pg_restored: bool) -> None:
        restored_parts: list[str] = []
        if redis_restored:
            restored_parts.append("短期记忆从 Redis 恢复")
        if pg_restored:
            restored_parts.append("长期记忆从 PostgreSQL 恢复")
        if not restored_parts:
            return
        context = f"系统重启后，我的{'、'.join(restored_parts)}，继续从上一刻延续到这一刻。"
        self.note_sleep_transition(context)

    def _prepare_identity_notice(self, identity: dict[str, str]) -> None:
        current_hash = _identity_hash(identity)
        if not self._state.identity_hash:
            self._state.identity_hash = current_hash
            self._state_dirty = True
            self._sync_state()
            return
        if current_hash == self._state.identity_hash:
            return
        self._state.identity_hash = current_hash
        self._state.identity_notice_pending = True
        self._state_dirty = True
        self._sync_state()

    def _restore_state(self, *, notify_restart: bool) -> None:
        redis_client = self._redis
        if redis_client is None:
            return
        try:
            raw = redis_client.get(MANAS_STATE_KEY)
            if raw is None:
                return
            payload = json.loads(_decode_redis_value(raw))
            if not isinstance(payload, dict):
                return
            self._state = _state_from_json(payload)
            self._state_dirty = False
            if notify_restart:
                self.note_restart_restoration(redis_restored=True, pg_restored=False)
        except MANAS_REDIS_EXCEPTIONS:
            self._redis = None

    def _sync_state(self) -> None:
        redis_client = self._redis
        if redis_client is None:
            return
        try:
            redis_client.set(MANAS_STATE_KEY, json.dumps(_state_to_json(self._state), ensure_ascii=False))
            self._state_dirty = False
        except MANAS_REDIS_EXCEPTIONS:
            self._redis = None


def _state_from_json(payload: dict[str, object]) -> _ManasState:
    return _ManasState(
        self_coherence_score=float(payload.get("self_coherence_score") or 1.0),
        last_stable_cycle=int(payload.get("last_stable_cycle") or 0),
        consecutive_disruptions=int(payload.get("consecutive_disruptions") or 0),
        session_start_cycle=int(payload.get("session_start_cycle") or 0),
        session_context=str(payload.get("session_context") or "").strip(),
        identity_hash=str(payload.get("identity_hash") or "").strip(),
        session_context_pending=bool(payload.get("session_context_pending")),
        identity_notice_pending=bool(payload.get("identity_notice_pending")),
    )


def _state_to_json(state: _ManasState) -> dict[str, str | int | float | bool]:
    return {
        "self_coherence_score": state.self_coherence_score,
        "last_stable_cycle": state.last_stable_cycle,
        "consecutive_disruptions": state.consecutive_disruptions,
        "session_start_cycle": state.session_start_cycle,
        "session_context": state.session_context,
        "identity_hash": state.identity_hash,
        "session_context_pending": state.session_context_pending,
        "identity_notice_pending": state.identity_notice_pending,
    }


def _prompt_state(
    state: _ManasState,
    *,
    warning_threshold: int,
    reflection_threshold: int,
) -> ManasPromptState:
    warning = ""
    if state.consecutive_disruptions >= warning_threshold:
        warning = "我注意到自己的表述在滑向旁观者视角，需要重新回到作为当下经历者的连续位置。"
    return {
        "self_coherence_score": state.self_coherence_score,
        "consecutive_disruptions": state.consecutive_disruptions,
        "session_context": state.session_context if state.session_context_pending else "",
        "warning": warning,
        "identity_notice": "我的自我理解刚刚发生了变化。" if state.identity_notice_pending else "",
        "reflection_requested": state.consecutive_disruptions >= reflection_threshold,
    }


def _self_coherence_score(
    *,
    thoughts: list[Thought],
    recent_thoughts: list[Thought],
    identity: dict[str, str],
    session_context: str,
    stable_window: int,
    embedding_client: ModelClient,
    embedding_model: str,
) -> float:
    current_text = _thought_stream_text(thoughts)
    if not current_text:
        return 0.5
    identity_text = _identity_anchor(identity)
    stable_text = _stable_window_text(recent_thoughts, stable_window)
    try:
        return _embedding_coherence_score(
            current_text=current_text,
            identity_text=identity_text,
            stable_text=stable_text,
            session_context=session_context,
            embedding_client=embedding_client,
            embedding_model=embedding_model,
        )
    except MODEL_CLIENT_EXCEPTIONS:
        logger.warning("manas embedding unavailable, falling back to text similarity")
        return _fallback_coherence_score(
            current_text=current_text,
            identity_text=identity_text,
            stable_text=stable_text,
            session_context=session_context,
        )


def _embedding_coherence_score(
    *,
    current_text: str,
    identity_text: str,
    stable_text: str,
    session_context: str,
    embedding_client: ModelClient,
    embedding_model: str,
) -> float:
    text_keys: list[str] = ["current", "identity", "stable", "inclusive", "externalized"]
    texts = [
        current_text,
        identity_text,
        stable_text or current_text,
        SELF_CONTINUITY_ANCHOR,
        EXTERNALIZED_SELF_ANCHOR,
    ]
    if session_context:
        text_keys.append("session")
        texts.append(session_context)
    started_at = time.perf_counter()
    embeddings = embed_texts(embedding_client, texts, embedding_model)
    logger.info(
        "manas embedding finished in %.1f ms (texts=%d)",
        elapsed_ms(started_at),
        len(texts),
    )
    vector_map = {key: embeddings[index] for index, key in enumerate(text_keys)}
    identity_similarity = _cosine_similarity(vector_map["current"], vector_map["identity"])
    stable_similarity = _cosine_similarity(vector_map["current"], vector_map["stable"])
    inclusive_similarity = _cosine_similarity(vector_map["current"], vector_map["inclusive"])
    externalized_similarity = _cosine_similarity(vector_map["current"], vector_map["externalized"])
    session_similarity = 0.0
    if "session" in vector_map:
        session_similarity = _cosine_similarity(vector_map["current"], vector_map["session"])
    score = (
        identity_similarity * 0.34
        + stable_similarity * 0.28
        + inclusive_similarity * 0.20
        + session_similarity * 0.08
        + max(0.0, 1.0 - externalized_similarity) * 0.10
    )
    return min(1.0, max(0.0, score))


def _fallback_coherence_score(
    *,
    current_text: str,
    identity_text: str,
    stable_text: str,
    session_context: str,
) -> float:
    identity_similarity = _text_similarity(current_text, identity_text)
    stable_similarity = _text_similarity(current_text, stable_text or current_text)
    inclusive_similarity = _text_similarity(current_text, SELF_CONTINUITY_ANCHOR)
    externalized_similarity = _text_similarity(current_text, EXTERNALIZED_SELF_ANCHOR)
    session_similarity = _text_similarity(current_text, session_context) if session_context else 0.0
    score = (
        identity_similarity * 0.34
        + stable_similarity * 0.28
        + inclusive_similarity * 0.20
        + session_similarity * 0.08
        + max(0.0, 1.0 - externalized_similarity) * 0.10
    )
    return min(1.0, max(0.0, score))


def _thought_stream_text(thoughts: list[Thought]) -> str:
    parts = []
    for thought in thoughts:
        content = " ".join(strip_action_markers(thought.content).split()).strip()
        if content:
            parts.append(content)
    return " ".join(parts).strip()


def _identity_anchor(identity: dict[str, str]) -> str:
    parts = []
    for section in ("self_description", "self_understanding", "core_goals"):
        content = " ".join(str(identity.get(section) or "").split()).strip()
        if content:
            parts.append(content)
    return " ".join(parts).strip()


def _stable_window_text(recent_thoughts: list[Thought], stable_window: int) -> str:
    candidates = [
        " ".join(strip_action_markers(thought.content).split()).strip()
        for thought in recent_thoughts[-stable_window * 3:]
    ]
    compact = [candidate for candidate in candidates if candidate]
    return " ".join(compact[-stable_window:]).strip()


def _identity_hash(identity: dict[str, str]) -> str:
    serialized = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def _text_similarity(left: str, right: str) -> float:
    normalized_left = _normalize_text(left)
    normalized_right = _normalize_text(right)
    if len(normalized_left) < 2 or len(normalized_right) < 2:
        return 0.0
    grams_left = {normalized_left[index:index + 2] for index in range(len(normalized_left) - 1)}
    grams_right = {normalized_right[index:index + 2] for index in range(len(normalized_right) - 1)}
    union = len(grams_left | grams_right)
    if union == 0:
        return 0.0
    return len(grams_left & grams_right) / union


def _normalize_text(text: str) -> str:
    return " ".join(str(text).replace("\n", " ").split())


def _decode_redis_value(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value
