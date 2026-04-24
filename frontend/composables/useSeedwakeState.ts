// Global reactive state for Seedwake UI.
//
// Sources:
// - Initial REST boot: /api/state, /api/thoughts, /api/actions,
//   /api/conversation, /api/stimuli (fetched in onMounted via useStream).
// - SSE /api/stream pushes typed events that mutate this state.

import type {
  ActionItem,
  ConversationEntry,
  RuntimeMode,
  SerializedThought,
  StateEventPayload,
  StimulusQueueItem,
} from "~/types/api";

export interface StreamItem {
  kind: "thought" | "separator";
  key: string;
  // thought fields (when kind === "thought")
  thought?: SerializedThought;
  attended?: boolean;
  // separator fields (when kind === "separator")
  cycle_id?: number;
  timestamp?: string;
}

// Rolling window: keep the last MAX_CYCLES cycles of thoughts.
// When a new thought arrives from cycle C, anything older than cycle C - MAX_CYCLES + 1
// is purged whole-cycle-at-a-time. This bounds memory regardless of uptime and
// enforces that observers can only see recent context (no scrollback history leak).
const MAX_CYCLES = 4;

function attendedByCycle(thoughts: SerializedThought[]): Map<number, string> {
  const best = new Map<number, { id: string; weight: number }>();
  for (const t of thoughts) {
    const cur = best.get(t.cycle_id);
    if (!cur || t.attention_weight > cur.weight) {
      best.set(t.cycle_id, { id: t.thought_id, weight: t.attention_weight });
    }
  }
  const out = new Map<number, string>();
  for (const [cid, v] of best) out.set(cid, v.id);
  return out;
}

function rebuildStream(thoughts: SerializedThought[]): StreamItem[] {
  const sorted = [...thoughts].sort((a, b) => {
    if (a.cycle_id !== b.cycle_id) return a.cycle_id - b.cycle_id;
    return a.index - b.index;
  });
  const attendedMap = attendedByCycle(sorted);
  const items: StreamItem[] = [];
  let lastCycle = -1;
  for (const t of sorted) {
    if (t.cycle_id !== lastCycle) {
      items.push({
        kind: "separator",
        key: `sep-${t.cycle_id}`,
        cycle_id: t.cycle_id,
        timestamp: t.timestamp,
      });
      lastCycle = t.cycle_id;
    }
    items.push({
      kind: "thought",
      key: t.thought_id,
      thought: t,
      attended: attendedMap.get(t.cycle_id) === t.thought_id,
    });
  }
  return items;
}

export function useSeedwakeState() {
  const mode = useState<RuntimeMode>("sw:mode", () => "waking");
  const state = useState<StateEventPayload | null>("sw:state", () => null);
  const thoughts = useState<SerializedThought[]>("sw:thoughts", () => []);
  const actions = useState<ActionItem[]>("sw:actions", () => []);
  const conversation = useState<ConversationEntry[]>("sw:conversation", () => []);
  const stimuli = useState<StimulusQueueItem[]>("sw:stimuli", () => []);
  const connected = useState<boolean>("sw:connected", () => false);
  const statusLog = useState<string[]>("sw:status_log", () => []);

  const streamItems = computed<StreamItem[]>(() => rebuildStream(thoughts.value));

  function ingestThoughts(incoming: SerializedThought[]) {
    // Merge by thought_id (dedupe), then drop whole cycles older than
    // (latest_cycle - MAX_CYCLES + 1).
    const byId = new Map<string, SerializedThought>();
    for (const t of thoughts.value) byId.set(t.thought_id, t);
    for (const t of incoming) byId.set(t.thought_id, t);
    const merged = [...byId.values()].sort((a, b) => {
      if (a.cycle_id !== b.cycle_id) return a.cycle_id - b.cycle_id;
      return a.index - b.index;
    });
    if (merged.length === 0) {
      thoughts.value = [];
      return;
    }
    const latestCycle = merged[merged.length - 1]!.cycle_id;
    const oldestAllowed = latestCycle - MAX_CYCLES + 1;
    thoughts.value = merged.filter((t) => t.cycle_id >= oldestAllowed);
  }

  function upsertAction(incoming: ActionItem) {
    // Keep terminal actions in state so historical thought cards can look up
    // their final status (succeeded/failed) instead of falling back to "pending".
    // ActionList (right panel) filters to non-terminal for the in-flight view.
    const idx = actions.value.findIndex((a) => a.action_id === incoming.action_id);
    if (idx >= 0) {
      const next = [...actions.value];
      next[idx] = { ...next[idx], ...incoming };
      actions.value = next;
    } else {
      actions.value = [...actions.value, incoming].slice(-50);
    }
  }

  function applyState(snapshot: StateEventPayload) {
    state.value = snapshot;
    mode.value = snapshot.mode;
  }

  function pushStatus(line: string) {
    statusLog.value = [...statusLog.value.slice(-19), line];
  }

  return {
    // state
    mode,
    state,
    thoughts,
    actions,
    conversation,
    stimuli,
    connected,
    statusLog,
    streamItems,
    // mutators
    ingestThoughts,
    upsertAction,
    applyState,
    pushStatus,
    setMode(next: RuntimeMode) {
      mode.value = next;
    },
    setConversation(items: ConversationEntry[]) {
      conversation.value = items.slice(-30);
    },
    appendConversationReply(speaker_name: string, content: string, timestamp: string) {
      const entry: ConversationEntry = {
        entry_id: `reply-${timestamp}`,
        role: "assistant",
        source: "telegram",
        content,
        timestamp,
        stimulus_id: null,
        metadata: {},
        direction: "outbound",
        speaker_name,
      };
      conversation.value = [...conversation.value, entry].slice(-30);
    },
    setStimuli(items: StimulusQueueItem[]) {
      // Same action can surface twice in the merged payload: once in pending
      // (`echo_current`) and once in the consumed cache (`echo_recent`), each
      // with a different stimulus_id. Dedupe first by stimulus_id, then by
      // action group key (source like "action:act_Cxxx-y"), preferring the
      // freshest bucket (noticed/echo_current > echo_recent).
      const BUCKET_RANK: Record<string, number> = {
        noticed: 0,
        echo_current: 0,
        echo_recent: 1,
      };
      const rank = (it: StimulusQueueItem) => BUCKET_RANK[it.bucket] ?? 99;
      const groupKey = (it: StimulusQueueItem): string => {
        // action-origin stimuli share a stable "action:act_..." source — use it
        // as the dedupe key across buckets. Fall back to stimulus_id for anything
        // without a stable action link.
        const src = (it.source || "").trim();
        return src.startsWith("action:") ? src : `stim:${it.stimulus_id}`;
      };
      const byGroup = new Map<string, StimulusQueueItem>();
      for (const item of items) {
        const key = groupKey(item);
        const existing = byGroup.get(key);
        if (!existing || rank(item) < rank(existing)) byGroup.set(key, item);
      }
      stimuli.value = [...byGroup.values()].sort(
        (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
      );
    },
    setActions(items: ActionItem[]) {
      // Backend returns newest-first (sorted by submitted_at DESC). Reverse to
      // oldest-first so it matches upsertAction's append-to-end convention;
      // then slice(-50) keeps the newest 50, not the oldest.
      const oldestFirst = [...items].reverse();
      actions.value = oldestFirst.slice(-50);
    },
  };
}
