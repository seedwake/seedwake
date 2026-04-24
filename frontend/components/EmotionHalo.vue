<script setup lang="ts">
import { EMOTION_DIMENSIONS, type StateEmotionsPayload } from "~/types/api";

const props = defineProps<{
  emotions: StateEmotionsPayload | null;
}>();

// Map dimension → CSS var (ink-drop tint on the cream background)
const DIM_COLORS: Record<keyof StateEmotionsPayload, string> = {
  curiosity: "var(--em-curiosity)",
  calm: "var(--em-calm)",
  satisfied: "var(--em-satisfied)",
  concern: "var(--em-concern)",
  frustration: "var(--em-frustration)",
};

// rings: outer ring = strongest dimension, inner rings stack by value desc.
// Each ring radius = base + value * scale.
const rings = computed(() => {
  const vals = props.emotions;
  if (!vals) return [];
  const sorted = [...EMOTION_DIMENSIONS].sort(
    (a, b) => (vals[b] || 0) - (vals[a] || 0),
  );
  return sorted.map((dim, i) => {
    const v = Math.max(0, Math.min(1, vals[dim] || 0));
    // radii: innermost 10, outermost ~85 (of 100 viewBox)
    const r = 16 + i * 14 + v * 6;
    const opacity = 0.18 + v * 0.5;
    // breathing period: higher value → shorter period (3s) down to longer (6s)
    const period = v < 0.2 ? 0 : (6 - v * 3).toFixed(2);
    return {
      dim,
      color: DIM_COLORS[dim],
      r,
      opacity: opacity.toFixed(3),
      period,
      value: v,
    };
  });
});

// center ink dot
const center = computed(() => {
  if (!props.emotions) return null;
  return "var(--ink)";
});
</script>

<template>
  <div class="emotion">
    <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
      <defs>
        <radialGradient id="sw-core" cx="50%" cy="50%" r="55%">
          <stop offset="0%" :stop-color="center || 'var(--ink)'" stop-opacity="0.85" />
          <stop offset="65%" stop-color="var(--ink-mute)" stop-opacity="0.2" />
          <stop offset="100%" stop-color="var(--ink-mute)" stop-opacity="0" />
        </radialGradient>
      </defs>

      <!-- concentric rings -->
      <g v-for="ring in rings" :key="ring.dim">
        <circle
          cx="50"
          cy="50"
          :r="ring.r"
          fill="none"
          :stroke="ring.color"
          stroke-width="0.8"
          :stroke-opacity="ring.opacity"
          :style="ring.period ? `animation: sw-ring-breathe ${ring.period}s ease-in-out infinite; transform-origin: 50% 50%;` : ''"
        />
      </g>

      <!-- center ink drop -->
      <circle
        v-if="center"
        cx="50"
        cy="50"
        r="8"
        fill="url(#sw-core)"
      />
    </svg>
  </div>
</template>

<style scoped>
@keyframes sw-ring-breathe {
  0%, 100% { transform: scale(1); opacity: 1; }
  50%      { transform: scale(1.04); opacity: 0.7; }
}
</style>
