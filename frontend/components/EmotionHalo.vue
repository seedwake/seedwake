<script setup lang="ts">
import type { StateEmotionsPayload } from "~/types/api";

const props = defineProps<{
  emotions: StateEmotionsPayload | null;
}>();

interface Dim {
  key: keyof StateEmotionsPayload;
  hue: number;
  l: number;
  c: number;
}

const DIMS: Dim[] = [
  { key: "curiosity",   hue: 72,  l: 0.55, c: 0.14 },
  { key: "calm",        hue: 245, l: 0.44, c: 0.10 },
  { key: "satisfied",   hue: 160, l: 0.52, c: 0.11 },
  { key: "concern",     hue: 330, l: 0.42, c: 0.09 },
  { key: "frustration", hue: 18,  l: 0.48, c: 0.15 },
];

// Concentric rings, one per dimension. Larger value → larger ring, thicker stroke,
// higher opacity, shorter breathing period. Rings are sorted by value ascending so
// that stacking order doesn't matter visually (they're stroke-only).
const ordered = computed(() => {
  const vals = props.emotions;
  if (!vals) return [];
  return DIMS
    .map((d) => ({ ...d, val: Math.max(0, Math.min(1, vals[d.key] || 0)) }))
    .sort((a, b) => a.val - b.val);
});

function radius(val: number): number {
  // Map [0, 1] → [14, 110] so even tiny values produce a visible inner ring
  // and strong values don't crash the edge of the 260×260 viewBox.
  return 14 + val * 96;
}

function strokeWidth(val: number): number {
  return 0.8 + val * 1.8; // 0.8 → 2.6
}

function strokeOpacity(val: number): number {
  return 0.35 + val * 0.45; // 0.35 → 0.80
}

// Breathing period: high value → 3s, low → 6s, < 0.08 → no animation.
function period(val: number): number {
  if (val < 0.08) return 0;
  return 3 + (1 - val) * 3;
}

function color(d: Dim): string {
  return `oklch(${d.l} ${d.c} ${d.hue})`;
}
</script>

<template>
  <div class="emotion">
    <svg viewBox="0 0 260 260" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <!-- static background: ryōan-ji rake rings as a water-ripple scale -->
      <g class="rake" fill="none" stroke="oklch(0.55 0.015 60 / 0.22)" stroke-width="0.3">
        <circle cx="130" cy="130" r="26" />
        <circle cx="130" cy="130" r="54" />
        <circle cx="130" cy="130" r="82" />
        <circle cx="130" cy="130" r="110" />
      </g>

      <!-- emotion rings -->
      <circle
        v-for="d in ordered"
        :key="d.key"
        cx="130" cy="130"
        :r="radius(d.val)"
        fill="none"
        :stroke="color(d)"
        :stroke-width="strokeWidth(d.val)"
        :stroke-opacity="strokeOpacity(d.val)"
        :class="['halo', `halo-${d.key}`, period(d.val) ? 'breathes' : '']"
        :style="period(d.val) ? `animation-duration: ${period(d.val).toFixed(2)}s;` : ''"
      />

      <!-- quiet center: tiny ink mark, no second ring to fight the halos -->
      <circle cx="130" cy="130" r="1.8" fill="oklch(0.3 0.01 60 / 0.65)" />
    </svg>
  </div>
</template>

<style scoped>
.halo {
  transform-origin: 130px 130px;
  transform-box: view-box;
}
.halo.breathes {
  animation-name: sw-ring-breathe;
  animation-iteration-count: infinite;
  animation-timing-function: ease-in-out;
}
/* stagger so rings don't all peak together */
.halo-curiosity   { animation-delay:  0s; }
.halo-calm        { animation-delay: -0.8s; }
.halo-satisfied   { animation-delay: -1.6s; }
.halo-concern     { animation-delay: -2.4s; }
.halo-frustration { animation-delay: -3.2s; }

@keyframes sw-ring-breathe {
  0%, 100% { transform: scale(0.97); opacity: 0.88; }
  50%      { transform: scale(1.03); opacity: 1; }
}
</style>
