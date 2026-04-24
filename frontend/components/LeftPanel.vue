<script setup lang="ts">
import { EMOTION_DIMENSIONS } from "~/types/api";

const { t } = useI18n();
const store = useSeedwakeState();

const emotions = computed(() => store.state.value?.emotions || null);
const cycle = computed(() => store.state.value?.cycle.current ?? 0);
const sinceBoot = computed(() => store.state.value?.cycle.since_boot ?? 0);
const avgSeconds = computed(() => {
  const v = store.state.value?.cycle.avg_seconds ?? 0;
  return v.toFixed(1);
});
const energy = computed(() => store.state.value?.energy ?? 0);
const energyPerCycle = computed(() => {
  const v = store.state.value?.energy_per_cycle ?? 0;
  return v.toFixed(1);
});
const nextDrowsy = computed(() => store.state.value?.next_drowsy_cycle ?? 0);
const startedAt = computed(() => store.state.value?.uptime.started_at || "");
const uptimeSeconds = computed(() => store.state.value?.uptime.seconds ?? 0);

const uptimeDisplay = computed(() => {
  const s = uptimeSeconds.value;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
});

const startedAtDisplay = computed(() => {
  if (!startedAt.value) return "";
  try {
    const d = new Date(startedAt.value);
    const date = d.toISOString().slice(0, 10);
    const time = d.toISOString().slice(11, 16);
    return `${date} · ${time}`;
  } catch {
    return startedAt.value;
  }
});

function emotionSwatch(dim: string) {
  return `var(--em-${dim})`;
}

const energyFillPercent = computed(() => {
  return Math.min(100, Math.max(0, energy.value));
});
</script>

<template>
  <aside class="col left">
    <header class="masthead">
      <span class="mark">Seed<i>wake</i></span>
      <span class="zh">{{ t("brand.zh_name") }}</span>
    </header>

    <div class="section-title">
      <span class="zh-big">{{ t("section.present") }}</span>
      <span class="en">{{ t("section.present_en") }}</span>
    </div>

    <ModeBadge :mode="store.mode.value" :cycle="cycle" />

    <EmotionHalo :emotions="emotions" />

    <div class="emotion-legend">
      <div class="legend-row" v-for="dim in EMOTION_DIMENSIONS" :key="dim">
        <div class="legend-name" :style="{ '--swatch': emotionSwatch(dim) }">
          <span>{{ t(`emotion.${dim}`) }}</span>
          <span class="tiny">{{ t(`emotion_en.${dim}`) }}</span>
        </div>
        <div class="legend-val">
          {{ emotions ? emotions[dim].toFixed(2) : "—" }}
        </div>
      </div>
    </div>

    <div class="meters">
      <div class="meter">
        <div class="head">
          <span class="zh">{{ t("meter.energy") }}</span>
          <span>{{ t("meter.energy_en") }}</span>
        </div>
        <div class="value">
          {{ energy.toFixed(0) }}<small>/ 100</small>
        </div>
        <div class="bar" :style="{ '--fill': `${energyFillPercent}%` }" />
        <div v-if="store.mode.value === 'light_sleep'" class="sub">
          {{ t("meter.drowsy_integrating") }}
        </div>
        <div v-else class="sub">
          {{ t("meter.next_drowsy", { per: energyPerCycle, cycle: nextDrowsy }) }}
        </div>
      </div>

      <div class="meter">
        <div class="head">
          <span class="zh">{{ t("meter.cycles") }}</span>
          <span>{{ t("meter.cycles_en") }}</span>
        </div>
        <div class="value">
          {{ sinceBoot.toLocaleString() }}<small>{{ t("meter.since_boot") }}</small>
        </div>
        <div class="sub">{{ t("meter.avg_seconds", { seconds: avgSeconds }) }}</div>
      </div>

      <div class="meter">
        <div class="head">
          <span class="zh">{{ t("meter.uptime") }}</span>
          <span>{{ t("meter.uptime_en") }}</span>
        </div>
        <div class="value">
          {{ uptimeDisplay }}<small>h · m</small>
        </div>
        <div class="sub">
          {{ t("meter.since_awakening", { timestamp: startedAtDisplay }) }}
        </div>
      </div>
    </div>

    <div class="footmark">{{ t("footmark") }}</div>
  </aside>
</template>
