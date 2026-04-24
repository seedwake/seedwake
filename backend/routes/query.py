"""Read/query routes for runtime state, thoughts, stimuli, and actions."""

import json
import logging
from typing import Annotated

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.deps import require_redis, resolve_api_client
from core.action import load_action_items
from core.memory.short_term import REDIS_KEY as THOUGHT_REDIS_KEY
from core.state import load_or_build_state_snapshot
from core.stimulus import (
    Stimulus,
    load_recent_action_echoes,
    load_stimulus_queue,
    pending_stimulus_bucket,
    stimulus_queue_item,
)
from core.common_types import (
    ActionsResponse,
    JsonObject,
    StateEventPayload,
    StimulusBucket,
    StimuliResponse,
    StimulusQueueItem,
    ThoughtsResponse,
    coerce_json_object,
)

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
REDIS_ROUTE_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
)
ApiClient = Annotated[str, Depends(resolve_api_client)]
ThoughtLimit = Annotated[int, Query(ge=1, le=300)]
ActionLimit = Annotated[int, Query(ge=1, le=300)]
StimulusLimit = Annotated[int, Query(ge=1, le=100)]
ActionStatusFilter = Annotated[str | None, Query()]
REDIS_UNAVAILABLE_RESPONSE = {
    503: {"description": "Redis unavailable"},
}
PENDING_STIMULUS_SCAN_LIMIT = 100
type BucketedStimulus = tuple[StimulusBucket, Stimulus]


@router.get("/state", responses=REDIS_UNAVAILABLE_RESPONSE)
def get_state(
    request: Request,
    api_client: ApiClient,
) -> StateEventPayload:
    _ = api_client
    redis_client = require_redis(request)
    config = coerce_json_object(request.app.state.config) or {}
    return load_or_build_state_snapshot(redis_client, config)


@router.get("/thoughts", responses=REDIS_UNAVAILABLE_RESPONSE)
def list_recent_thoughts(
    request: Request,
    api_client: ApiClient,
    limit: ThoughtLimit = 60,
) -> ThoughtsResponse:
    redis_client = require_redis(request)
    items = _load_recent_thoughts(redis_client, limit)
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "requested_by": api_client,
    }


@router.get("/actions", responses=REDIS_UNAVAILABLE_RESPONSE)
def list_actions(
    request: Request,
    api_client: ApiClient,
    limit: ActionLimit = 100,
    status: ActionStatusFilter = None,
) -> ActionsResponse:
    redis_client = require_redis(request)
    items = [_action_response_item(item) for item in _load_action_items(redis_client)]
    if status:
        allowed = {part.strip() for part in status.split(",") if part.strip()}
        items = [item for item in items if str(item.get("status") or "") in allowed]
    items.sort(key=lambda item: str(item.get("submitted_at") or ""), reverse=True)
    items = items[:limit]
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "requested_by": api_client,
    }


@router.get("/stimuli", responses=REDIS_UNAVAILABLE_RESPONSE)
def list_stimuli(
    request: Request,
    api_client: ApiClient,
    limit: StimulusLimit = 20,
) -> StimuliResponse:
    redis_client = require_redis(request)
    config = coerce_json_object(request.app.state.config) or {}
    current_cycle_id = load_or_build_state_snapshot(redis_client, config)["cycle"]["current"]
    try:
        pending_stimuli = load_stimulus_queue(redis_client, PENDING_STIMULUS_SCAN_LIMIT)
        consumed_stimuli = load_recent_action_echoes(
            redis_client,
            current_cycle_id=current_cycle_id,
            exclude_action_ids=None,
        )
    except REDIS_ROUTE_EXCEPTIONS as exc:
        raise HTTPException(status_code=503, detail=f"redis read failed: {exc}") from exc
    items = _merged_stimulus_items(pending_stimuli, consumed_stimuli, limit)
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "requested_by": api_client,
    }


def _load_action_items(redis_client) -> list[JsonObject]:
    try:
        return load_action_items(redis_client)
    except REDIS_ROUTE_EXCEPTIONS as exc:
        raise HTTPException(status_code=503, detail=f"redis read failed: {exc}") from exc


def _load_recent_thoughts(redis_client, limit: int) -> list[JsonObject]:
    try:
        raw_items = redis_client.zrange(THOUGHT_REDIS_KEY, -limit, -1)
    except REDIS_ROUTE_EXCEPTIONS as exc:
        raise HTTPException(status_code=503, detail=f"redis read failed: {exc}") from exc
    items = []
    for raw in raw_items:
        try:
            item = json.loads(raw)
        except (TypeError, ValueError) as exc:
            logger.warning("skipping malformed thought record: %s", exc)
            continue
        if not isinstance(item, dict):
            logger.warning("skipping non-object thought record")
            continue
        items.append(item)
    return items


def _action_response_item(item: JsonObject) -> JsonObject:
    response_item = dict(item)
    response_item["summary"] = _action_response_summary(item)
    result = response_item.get("result")
    if isinstance(result, dict):
        response_item["result"] = _public_action_result(result)
    return response_item


def _public_action_result(result: JsonObject) -> JsonObject:
    public_result = dict(result)
    public_result.pop("summary_key", None)
    public_result.pop("summary_params", None)
    return public_result


def _action_response_summary(item: JsonObject) -> JsonObject:
    result = item.get("result")
    if isinstance(result, dict):
        key = str(result.get("summary_key") or "").strip()
        params = result.get("summary_params")
        if key:
            return {
                "key": key,
                "params": coerce_json_object(params) or {},
            }
        summary = str(result.get("summary") or "").strip()
        if summary:
            return {
                "key": "action.completed_with_summary",
                "params": {"summary": summary},
            }
    if bool(item.get("awaiting_confirmation")):
        return {"key": "action.awaiting_status", "params": {}}
    status = str(item.get("status") or "").strip()
    if status == "running":
        return {"key": "action.running_status", "params": {}}
    if status == "pending":
        return {"key": "action.submitted_status", "params": {}}
    return {"key": "action.completed_default", "params": {}}


def _merged_stimulus_items(
    pending_stimuli: list[Stimulus],
    consumed_stimuli: list[Stimulus],
    limit: int,
) -> list[StimulusQueueItem]:
    bucketed_stimuli: list[BucketedStimulus] = [
        *_bucketed_pending_stimuli(pending_stimuli),
        *[("echo_recent", stimulus) for stimulus in consumed_stimuli],
    ]
    bucketed_stimuli = _dedup_bucketed_stimuli(bucketed_stimuli)
    bucketed_stimuli.sort(key=lambda item: item[1].timestamp, reverse=True)
    return [
        stimulus_queue_item(stimulus, bucket)
        for bucket, stimulus in bucketed_stimuli[:limit]
    ]


def _dedup_bucketed_stimuli(bucketed_items: list[BucketedStimulus]) -> list[BucketedStimulus]:
    selected: dict[str, BucketedStimulus] = {}
    for item in bucketed_items:
        key = _stimulus_dedup_key(item[1])
        existing = selected.get(key)
        if existing is None or _bucketed_stimulus_is_preferred(item, existing):
            selected[key] = item
    return list(selected.values())


def _stimulus_dedup_key(stimulus: Stimulus) -> str:
    source = str(stimulus.source or "").strip()
    if source.startswith("action:"):
        return source
    return f"stimulus:{stimulus.stimulus_id}"


def _bucketed_stimulus_is_preferred(
    candidate: BucketedStimulus,
    existing: BucketedStimulus,
) -> bool:
    candidate_priority = _bucket_preference(candidate[0])
    existing_priority = _bucket_preference(existing[0])
    if candidate_priority != existing_priority:
        return candidate_priority > existing_priority
    return candidate[1].timestamp > existing[1].timestamp


def _bucket_preference(bucket: StimulusBucket) -> int:
    if bucket == "echo_current":
        return 3
    if bucket == "noticed":
        return 2
    return 1


def _bucketed_pending_stimuli(
    stimuli: list[Stimulus],
) -> list[BucketedStimulus]:
    bucketed: list[BucketedStimulus] = []
    for stimulus in stimuli:
        bucket = pending_stimulus_bucket(stimulus)
        if bucket is not None:
            bucketed.append((bucket, stimulus))
    return bucketed
