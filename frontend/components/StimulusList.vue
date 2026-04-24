<script setup lang="ts">
import type { StimulusQueueItem } from "~/types/api";

const props = defineProps<{ stimuli: StimulusQueueItem[] }>();
const { t, te } = useI18n();
const store = useSeedwakeState();

// useSeedwakeState.setStimuli normalizes incoming items to ASC by timestamp
// (oldest first), matching the conversation/action panel convention. So we
// just take the trailing N — that gives the newest 10 in oldest-first order,
// ready for top-to-bottom render with newest at the bottom.
const MAX_ITEMS = 10;
const ACTION_PREFIX = "action:";

// send_message echoes are redundant here — the outbound message already
// appears in the conversation panel. Cross-reference the stimulus's action
// source against the actions store to hide them.
const sendMessageActionIds = computed<Set<string>>(() => {
  const ids = new Set<string>();
  for (const a of store.actions.value) {
    if (a.type === "send_message") ids.add(a.action_id);
  }
  return ids;
});

const displayItems = computed(() => {
  const filtered = props.stimuli.filter((s) => {
    const src = (s.source || "").trim();
    if (!src.startsWith(ACTION_PREFIX)) return true;
    const actionId = src.slice(ACTION_PREFIX.length);
    return !sendMessageActionIds.value.has(actionId);
  });
  return filtered.slice(-MAX_ITEMS);
});

const panelRef = ref<HTMLElement | null>(null);
// Auto-scroll signal tracks the newest item's timestamp, not just length — so
// when a fresh stimulus replaces an old one (cap stays at 10) we still scroll.
const { isOverflowing } = useAutoScroll(panelRef, () => {
  const arr = displayItems.value;
  if (arr.length === 0) return 0;
  return new Date(arr[arr.length - 1]!.timestamp).getTime();
});

function typeLabel(type: string): string {
  const key = `stimulus_type.${type}`;
  return te(key) ? t(key) : type;
}

function relativeTime(ts: string): string {
  try {
    const when = new Date(ts).getTime();
    const diff = Math.max(0, Date.now() - when);
    const secs = Math.round(diff / 1000);
    if (secs < 60) return `${secs} s ago`;
    const mins = Math.round(secs / 60);
    if (mins < 60) return `${mins} m ago`;
    const hrs = Math.round(mins / 60);
    return `${hrs} h ago`;
  } catch {
    return "";
  }
}
</script>

<template>
  <div class="panel">
    <div class="eyebrow">
      <span class="zh">{{ t("right.stimulus_label") }}</span>
      <span>{{ t("right.stimulus_label_en") }}</span>
    </div>
    <div class="scroll" ref="panelRef" :class="{ 'edge-fade': isOverflowing }">
      <p v-if="displayItems.length === 0" class="msg">
        <span class="text" style="color: var(--ink-faint)">{{ t("right.empty_stimuli") }}</span>
      </p>
      <div
        v-for="s in displayItems"
        :key="s.stimulus_id"
        class="action-row"
        :data-state="s.bucket === 'echo_recent' ? 'done' : 'pending'"
      >
        <div class="kind">
          {{ typeLabel(s.type) }}<template v-if="s.source"> · {{ s.source }}</template>
        </div>
        <div class="state">
          <span class="sd" /> {{ t("right.priority", { n: s.priority }) }}
        </div>
        <div class="detail">"{{ s.summary }}" · {{ relativeTime(s.timestamp) }}</div>
      </div>
    </div>
  </div>
</template>
