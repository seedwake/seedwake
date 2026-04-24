<script setup lang="ts">
import type { StateEmotionsPayload } from "~/types/api";

const props = defineProps<{
  emotions: StateEmotionsPayload | null;
}>();

interface Dim {
  key: keyof StateEmotionsPayload;
  hue: number;
  angle: number;      // SVG degrees: -90 = top, 0 = right
  seed: number;       // per-drop turbulence seed
  breatheSec: number; // breathing period; slight variation makes the field feel alive
}

// Five colored ink drops placed around the center at 72° apart, starting from top.
//   top      curiosity   (active, outward)
//   upper-R  satisfied   (restful, positive)
//   lower-R  calm        (grounded, positive)
//   lower-L  concern     (grounded, negative)
//   upper-L  frustration (high energy, negative)
const DIMS: Dim[] = [
  { key: "curiosity",   hue: 72,  angle: -90, seed: 3,  breatheSec: 6.4 },
  { key: "satisfied",   hue: 160, angle: -18, seed: 7,  breatheSec: 7.3 },
  { key: "calm",        hue: 245, angle:  54, seed: 11, breatheSec: 8.1 },
  { key: "concern",     hue: 330, angle: 126, seed: 17, breatheSec: 6.9 },
  { key: "frustration", hue: 18,  angle: 198, seed: 23, breatheSec: 7.6 },
];

const CENTER = 130;
const ORBIT = 74;
const DROP_MIN = 18;
const DROP_MAX = 48;

interface Drop {
  key: string;
  hue: number;
  cx: number;
  cy: number;
  r: number;
  val: number;
  seed: number;
  breatheSec: number;
}

const drops = computed<Drop[]>(() => {
  const vals = props.emotions;
  if (!vals) return [];
  return DIMS.map((d) => {
    const v = Math.max(0, Math.min(1, vals[d.key] || 0));
    const rad = (d.angle * Math.PI) / 180;
    return {
      key: d.key,
      hue: d.hue,
      cx: CENTER + ORBIT * Math.cos(rad),
      cy: CENTER + ORBIT * Math.sin(rad),
      r: DROP_MIN + v * (DROP_MAX - DROP_MIN),
      val: v,
      seed: d.seed,
      breatheSec: d.breatheSec,
    };
  });
});

function gradId(key: string): string { return `sw-drop-grad-${key}`; }
function filterId(key: string): string { return `sw-drop-edge-${key}`; }

// Slow clockwise rotation with a damped-spring boost on data updates,
// so an emotion shift reads like a live disturbance rippling through the halo.
const rotation = ref(0);
const boostPos = ref(0);
const boostVel = ref(0);
const BASE_SPEED = 2.6;  // deg/sec → ~140s per revolution
const STIFFNESS = 4;
const DAMPING = 4;       // critically damped: rise → peak → smooth return, no oscillation

let rafId: number | null = null;
let lastTime = 0;

function tick(now: number): void {
  if (lastTime) {
    const dt = Math.min(0.05, (now - lastTime) / 1000);
    const force = -STIFFNESS * boostPos.value - DAMPING * boostVel.value;
    boostVel.value += force * dt;
    boostPos.value += boostVel.value * dt;
    if (Math.abs(boostPos.value) < 0.01 && Math.abs(boostVel.value) < 0.01) {
      boostPos.value = 0;
      boostVel.value = 0;
    }
    const speed = BASE_SPEED + Math.max(0, boostPos.value);
    rotation.value = (rotation.value + speed * dt) % 360;
  }
  lastTime = now;
  rafId = requestAnimationFrame(tick);
}

watch(() => props.emotions, (next, prev) => {
  if (!next || !prev) return;
  let delta = 0;
  for (const d of DIMS) {
    delta += Math.abs((next[d.key] || 0) - (prev[d.key] || 0));
  }
  if (delta > 0.015) {
    boostVel.value += Math.min(delta * 220, 400);
  }
}, { deep: true });

onMounted(() => {
  lastTime = 0;
  rafId = requestAnimationFrame(tick);
});

onBeforeUnmount(() => {
  if (rafId !== null) cancelAnimationFrame(rafId);
});

const rotationTransform = computed(
  () => `rotate(${rotation.value.toFixed(2)} ${CENTER} ${CENTER})`
);
</script>

<template>
  <div class="emotion">
    <svg viewBox="0 0 260 260" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <defs>
        <!-- per-drop radial gradient: dense pigment center → feathered transparent edge -->
        <radialGradient
          v-for="d in drops"
          :key="`${d.key}-grad`"
          :id="gradId(d.key)"
          cx="50%" cy="50%" r="50%"
        >
          <stop offset="0%"   :stop-color="`oklch(0.42 0.16 ${d.hue})`" stop-opacity="0.95" />
          <stop offset="35%"  :stop-color="`oklch(0.5 0.14 ${d.hue})`"  stop-opacity="0.7" />
          <stop offset="75%"  :stop-color="`oklch(0.62 0.08 ${d.hue})`" stop-opacity="0.25" />
          <stop offset="100%" :stop-color="`oklch(0.72 0.04 ${d.hue})`" stop-opacity="0" />
        </radialGradient>

        <!-- per-drop paper-bleed filter: turbulence displaces the edge irregularly -->
        <filter
          v-for="d in drops"
          :key="`${d.key}-filter`"
          :id="filterId(d.key)"
          x="-30%" y="-30%" width="160%" height="160%"
        >
          <feTurbulence type="fractalNoise" baseFrequency="0.75" numOctaves="2" :seed="d.seed" result="noise" />
          <feDisplacementMap in="SourceGraphic" in2="noise" scale="5" />
        </filter>
      </defs>

      <!-- faint reference circle at the drop orbit (stationary) -->
      <circle cx="130" cy="130" r="74"
              fill="none"
              stroke="oklch(0.55 0.015 60 / 0.18)"
              stroke-width="0.4"
              stroke-dasharray="2 3" />

      <!-- rotating group so the whole halo drifts clockwise -->
      <g :transform="rotationTransform">
        <circle
          v-for="d in drops"
          :key="d.key"
          :cx="d.cx" :cy="d.cy"
          :r="d.r"
          :fill="`url(#${gradId(d.key)})`"
          :filter="`url(#${filterId(d.key)})`"
          :class="['drop', `drop-${d.key}`]"
          :style="`animation-duration: ${d.breatheSec.toFixed(2)}s;`"
        />
      </g>
    </svg>
  </div>
</template>

<style scoped>
.drop {
  transform-origin: center;
  transform-box: fill-box;
  /* smooth size animation when a new emotion value arrives */
  transition: r 1.4s cubic-bezier(0.22, 0.8, 0.24, 1);
  animation-name: sw-drop-breathe;
  animation-iteration-count: infinite;
  animation-timing-function: ease-in-out;
}
/* stagger so drops don't all pulse in phase */
.drop-curiosity   { animation-delay:  0s; }
.drop-satisfied   { animation-delay: -1.4s; }
.drop-calm        { animation-delay: -2.8s; }
.drop-concern     { animation-delay: -4.2s; }
.drop-frustration { animation-delay: -5.6s; }

@keyframes sw-drop-breathe {
  0%, 100% { transform: scale(0.88); opacity: 0.88; }
  50%      { transform: scale(1.1);  opacity: 1; }
}
</style>
