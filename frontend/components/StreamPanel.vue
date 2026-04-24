<script setup lang="ts">
import type { ActionItem } from "~/types/api";
import type { StreamItem } from "~/composables/useSeedwakeState";

const { t } = useI18n();
const resolveI18nText = useI18nText();
const store = useSeedwakeState();

// Drip-feed reveal: raw items (from the store) are the source of truth, but we
// release them into the rendered list one at a time so a multi-thought cycle
// doesn't pop in as a pre-allocated block. Each thought waits THOUGHT_INTERVAL_MS
// after the previous one; separators (cycle dividers) release immediately so
// they arrive paired with the first thought of their cycle.
const rawItems = computed<StreamItem[]>(() => store.streamItems.value);
const visibleItems = ref<StreamItem[]>([]);
const THOUGHT_INTERVAL_MS = 3000;

const streamRef = ref<HTMLElement | null>(null);
// smooth:true so each release glides the viewport to the new bottom rather than
// jump-cutting. First population still uses instant scroll inside useAutoScroll.
useAutoScroll(streamRef, () => visibleItems.value.length, { smooth: true });

let releaseTimer: ReturnType<typeof setTimeout> | null = null;
let initialReleaseDone = false;

function pendingFromRaw(): StreamItem[] {
  const have = new Set(visibleItems.value.map((v) => v.key));
  return rawItems.value.filter((v) => !have.has(v.key));
}

function syncRemovals(): void {
  const rawKeys = new Set(rawItems.value.map((v) => v.key));
  visibleItems.value = visibleItems.value.filter((v) => rawKeys.has(v.key));
}

function releaseOne(): void {
  releaseTimer = null;
  const pending = pendingFromRaw();
  if (pending.length === 0) return;
  const item = pending[0]!;
  visibleItems.value = [...visibleItems.value, item];
  if (pendingFromRaw().length > 0) {
    // separators are just cycle dividers — no dwell time before the next thought
    const delay = item.kind === "separator" ? 0 : THOUGHT_INTERVAL_MS;
    releaseTimer = setTimeout(releaseOne, delay);
  }
}

watch(rawItems, () => {
  syncRemovals();
  if (pendingFromRaw().length === 0) return;
  if (!initialReleaseDone) {
    // first populate (SSR hydrate / SSE initial snapshot) — release the whole
    // rolling window at once so history doesn't drip-feed on page load
    visibleItems.value = [...rawItems.value];
    initialReleaseDone = true;
    return;
  }
  if (releaseTimer !== null) return; // drip loop already running
  releaseOne();
}, { immediate: true });

onBeforeUnmount(() => {
  if (releaseTimer !== null) clearTimeout(releaseTimer);
});

// data-vi drives the per-card opacity ramp — newest = 5 (full), cascading back to 0.
// Items further than 5 back stay at 0, which combined with the top mask reads as
// "fading into the past" without hiding content entirely.
function viForItem(index: number): number {
  const total = visibleItems.value.length;
  return Math.max(0, 5 - (total - 1 - index));
}

// Match an action to a thought by source_thought_id.
function actionForThought(
  actions: ActionItem[],
  thoughtId: string,
): { state: string; summary: string } | undefined {
  const match = actions.find((a) => a.source_thought_id === thoughtId);
  if (!match) return undefined;
  return {
    state: match.status,
    summary: resolveI18nText(match.summary),
  };
}

const counter = computed(() => {
  const mode = store.mode.value;
  if (mode === "light_sleep") {
    const c = store.state.value?.cycle.current ?? 0;
    return t("stream_foot.counter_paused", { cycle: c });
  }
  if (mode === "deep_sleep") {
    return t("stream_foot.counter_deep");
  }
  // waking — show attended-thought counter if available.
  // Use visibleItems (not raw) so the counter tracks what the viewer actually
  // sees, not what's been queued for drip-release.
  const items = visibleItems.value;
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const it = items[i];
    if (it && it.kind === "thought" && it.attended && it.thought) {
      return t("stream_foot.counter_attended", {
        thought_id: `C${it.thought.cycle_id}-${it.thought.index}`,
      });
    }
  }
  const c = store.state.value?.cycle.current ?? 0;
  return t("stream_foot.counter_streaming", { cycle: c });
});

const streamLabel = computed(() => {
  if (store.mode.value === "light_sleep") return t("stream_foot.paused");
  return t("stream_foot.streaming");
});

const drowsyBanner = computed(() => {
  if (store.mode.value !== "light_sleep") return null;
  const c = store.state.value?.cycle.current ?? 0;
  return t("stream_foot.drowsy_banner", { cycle: c });
});

const resumeHint = computed(() => {
  if (store.mode.value !== "light_sleep") return null;
  return t("stream_foot.resume_hint", { eta: "02:18" });
});
</script>

<template>
  <section class="col stream-col">
    <header class="stream-head">
      <h1>{{ t("section.stream") }}</h1>
      <span class="counter">{{ counter }}</span>
    </header>
    <div class="stream" ref="streamRef">
      <div class="thoughts">
        <template v-for="(item, i) in visibleItems" :key="item.key">
          <CycleSeparator
            v-if="item.kind === 'separator'"
            :cycle-id="item.cycle_id!"
            :timestamp="item.timestamp"
          />
          <ThoughtCard
            v-else-if="item.thought"
            :thought="item.thought"
            :attended="!!item.attended"
            :visual-index="viForItem(i)"
            :action-status="actionForThought(store.actions.value, item.thought.thought_id)"
          />
        </template>
      </div>
    </div>
    <!-- Banner lives outside the scroll container so it stays pinned to the
         column bottom regardless of scrollTop. -->
    <div v-if="drowsyBanner" class="drowsy-banner">
      <span>{{ drowsyBanner }}</span>
      <small>{{ resumeHint }}</small>
    </div>
    <div class="stream-foot">
      <span class="live">
        <span class="beat" />
        <span>{{ streamLabel }}</span>
      </span>
      <span>{{ t("stream_foot.sse_types") }}</span>
    </div>
  </section>
</template>
