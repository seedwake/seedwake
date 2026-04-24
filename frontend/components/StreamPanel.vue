<script setup lang="ts">
import type { ActionItem } from "~/types/api";

const { t } = useI18n();
const resolveI18nText = useI18nText();
const store = useSeedwakeState();

// Render every thought in the rolling window so the user can scroll up to see history;
// auto-scroll keeps the view pinned to the latest when they're already at the bottom.
const visibleItems = computed(() => store.streamItems.value);

const streamRef = ref<HTMLElement | null>(null);
useAutoScroll(streamRef, () => visibleItems.value.length);

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
  // waking — show attended-thought counter if available
  const items = store.streamItems.value;
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
            :style="`--enter-delay: ${(item.thought.index - 1) * 240}ms`"
          />
        </template>
      </div>
      <div v-if="drowsyBanner" class="drowsy-banner">
        <span>{{ drowsyBanner }}</span>
        <small>{{ resumeHint }}</small>
      </div>
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
