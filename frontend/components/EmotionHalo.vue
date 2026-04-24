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
  // orbit shrinks toward center on hover; drop radius (r) stays unchanged so
  // the ink spots keep their pigment size, only their positions draw inward.
  const effectiveOrbit = ORBIT * (1 - HOVER_SHRINK * hoverFactor.value);
  return DIMS.map((d) => {
    const v = Math.max(0, Math.min(1, vals[d.key] || 0));
    const rad = (d.angle * Math.PI) / 180;
    return {
      key: d.key,
      hue: d.hue,
      cx: CENTER + effectiveOrbit * Math.cos(rad),
      cy: CENTER + effectiveOrbit * Math.sin(rad),
      r: DROP_MIN + v * (DROP_MAX - DROP_MIN),
      val: v,
      seed: d.seed,
      breatheSec: d.breatheSec,
    };
  });
});

const effectiveRefOrbit = computed(() => ORBIT * (1 - HOVER_SHRINK * hoverFactor.value));

function gradId(key: string): string { return `sw-drop-grad-${key}`; }
function filterId(key: string): string { return `sw-drop-edge-${key}`; }

// Slow clockwise rotation with a damped-spring boost on data updates,
// so an emotion shift reads like a live disturbance rippling through the halo.
const rotation = ref(0);
const boostPos = ref(0);
const boostVel = ref(0);
const BASE_SPEED = 4.0;  // deg/sec → ~90s per revolution (quicker idle)
const STIFFNESS = 0.04;  // very soft spring
const DAMPING = 0.4;     // critical damping for k=0.04 (c = 2√k ≈ 0.4): no
                         // oscillation, peak at ~5s, fully settled ~25s

// Hover interaction: cursor entering the halo eases rotation down to ~30% of
// idle AND smoothly contracts the whole halo 20% toward its center — "your
// gaze stills the stream, and the stream draws inward".
const hoverFactor = ref(0);          // 0 = idle, 1 = fully hovered, lerped in tick()
let hoverTarget = 0;                 // step target for hoverFactor
const HOVER_SPEED_MIN = 0.5;         // rotation scales to 50% at full hover
const HOVER_SHRINK = 0.2;            // halo shrinks by this fraction at full hover
const HOVER_LERP = 4;                // how fast hoverFactor approaches target

let rafId: number | null = null;
let lastTime = 0;

function tick(now: number): void {
  if (lastTime) {
    const dt = Math.min(0.05, (now - lastTime) / 1000);
    // spring for disturbance boost
    const force = -STIFFNESS * boostPos.value - DAMPING * boostVel.value;
    boostVel.value += force * dt;
    boostPos.value += boostVel.value * dt;
    if (Math.abs(boostPos.value) < 0.01 && Math.abs(boostVel.value) < 0.01) {
      boostPos.value = 0;
      boostVel.value = 0;
    }
    // hover factor eases toward target
    hoverFactor.value += (hoverTarget - hoverFactor.value) * Math.min(1, HOVER_LERP * dt);
    // rotation speed: base scaled by hover, plus disturbance boost (never negative)
    const speedFactor = 1 - (1 - HOVER_SPEED_MIN) * hoverFactor.value;
    const speed = BASE_SPEED * speedFactor + Math.max(0, boostPos.value);
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
    // large kick so the burst is visibly dramatic (~73 deg/s peak for a delta
    // of 0.5, about 18x the idle speed). Same soft spring → still ~25s total
    // settle, and the bigger amplitude keeps the acceleration readable for
    // much longer above any perceptual threshold.
    boostVel.value += Math.min(delta * 80, 200);
  }
}, { deep: true });

function onMouseEnter(): void { hoverTarget = 1; }
function onMouseLeave(): void { hoverTarget = 0; }

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
    <svg
      viewBox="0 0 260 260"
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
      @mouseenter="onMouseEnter"
      @mouseleave="onMouseLeave"
    >
      <defs>
        <!-- Iterate over the static DIMS (not the value-bound `drops` computed) so
             these defs mount once and are never re-evaluated on emotion updates —
             otherwise Vue would re-run the v-for on every value change and the
             SVG gradients/filters would visually flicker. -->
        <radialGradient
          v-for="d in DIMS"
          :key="`${d.key}-grad`"
          :id="gradId(d.key)"
          cx="50%" cy="50%" r="50%"
        >
          <stop offset="0%"   :stop-color="`oklch(0.42 0.16 ${d.hue})`" stop-opacity="0.95" />
          <stop offset="35%"  :stop-color="`oklch(0.5 0.14 ${d.hue})`"  stop-opacity="0.7" />
          <stop offset="75%"  :stop-color="`oklch(0.62 0.08 ${d.hue})`" stop-opacity="0.25" />
          <stop offset="100%" :stop-color="`oklch(0.72 0.04 ${d.hue})`" stop-opacity="0" />
        </radialGradient>

        <filter
          v-for="d in DIMS"
          :key="`${d.key}-filter`"
          :id="filterId(d.key)"
          x="-30%" y="-30%" width="160%" height="160%"
        >
          <feTurbulence type="fractalNoise" baseFrequency="0.75" numOctaves="2" :seed="d.seed" result="noise" />
          <feDisplacementMap in="SourceGraphic" in2="noise" scale="5" />
        </filter>
      </defs>

      <!-- faint reference circle at the drop orbit; shrinks with the halo on hover -->
      <circle cx="130" cy="130" :r="effectiveRefOrbit"
              fill="none"
              stroke="oklch(0.55 0.015 60 / 0.18)"
              stroke-width="0.4"
              stroke-dasharray="2 3" />

      <!-- rotating group around halo center; orbit radius itself contracts via
           drops' cx/cy on hover, so drops keep their pigment size -->
      <g :transform="rotationTransform">
        <circle
          v-for="d in drops"
          :key="d.key"
          :cx="d.cx" :cy="d.cy"
          :r="d.r"
          :fill="`url(#${gradId(d.key)})`"
          :filter="`url(#${filterId(d.key)})`"
          :class="['drop', `drop-${d.key}`]"
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
/* Per-dim breathing rhythm: duration + phase offset baked into CSS so neither
   survives Vue re-renders as an inline-style churn (which would restart the
   animation from scratch every emotion update). */
.drop-curiosity   { animation-duration: 6.4s; animation-delay:  0s; }
.drop-satisfied   { animation-duration: 7.3s; animation-delay: -1.4s; }
.drop-calm        { animation-duration: 8.1s; animation-delay: -2.8s; }
.drop-concern     { animation-duration: 6.9s; animation-delay: -4.2s; }
.drop-frustration { animation-duration: 7.6s; animation-delay: -5.6s; }

@keyframes sw-drop-breathe {
  0%, 100% { transform: scale(0.88); opacity: 0.88; }
  50%      { transform: scale(1.1);  opacity: 1; }
}
</style>
