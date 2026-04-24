<script setup lang="ts">
import type { StimulusQueueItem } from "~/types/api";

const props = defineProps<{ stimuli: StimulusQueueItem[] }>();
const { t, te } = useI18n();

const panelRef = ref<HTMLElement | null>(null);
const { isOverflowing } = useAutoScroll(panelRef, () => props.stimuli.length);

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
      <p v-if="stimuli.length === 0" class="msg">
        <span class="text" style="color: var(--ink-faint)">{{ t("right.empty_stimuli") }}</span>
      </p>
      <div
        v-for="s in stimuli"
        :key="s.stimulus_id"
        class="action-row"
        data-state="pending"
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
