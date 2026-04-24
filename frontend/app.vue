<script setup lang="ts">
const store = useSeedwakeState();
const stream = useStream();
const route = useRoute();
const config = useRuntimeConfig();
const { setLocale, locale } = useI18n();

// Language selection: ?lang=xx overrides; else env; else whatever the cookie set
const queryLang = computed(() => {
  const v = route.query.lang;
  const raw = Array.isArray(v) ? v[0] : v;
  return raw === "zh" || raw === "en" ? raw : null;
});

// Resolve locale on both server and client so SSR output matches the chosen language.
const preferred = queryLang.value || (config.public.language as string | undefined);
if (preferred && preferred !== locale.value && (preferred === "zh" || preferred === "en")) {
  await setLocale(preferred);
}

onMounted(() => {
  stream.connect();
});

onBeforeUnmount(() => {
  stream.disconnect();
});

// Keep <html lang="..."> in sync so CSS font selectors work.
useHead({
  htmlAttrs: {
    lang: () => (locale.value === "en" ? "en" : "zh-Hans"),
  },
  bodyAttrs: {
    class: () => bodyClassForMode(store.mode.value),
  },
});

function bodyClassForMode(mode: string): string {
  if (mode === "light_sleep") return "drowsy";
  if (mode === "deep_sleep") return "deep";
  return "waking";
}

const isDev = import.meta.dev;
</script>

<template>
  <div>
    <!-- mode-dependent backdrop overlay -->
    <div class="mode-bloom" aria-hidden="true" />

    <!-- SVG filter: irregular seal-edge bite -->
    <svg width="0" height="0" style="position:absolute" aria-hidden="true">
      <filter id="seal-bite" x="-10%" y="-10%" width="120%" height="120%">
        <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="3" />
        <feDisplacementMap in="SourceGraphic" scale="1.6" />
      </filter>
    </svg>

    <div class="app">
      <LeftPanel />
      <StreamPanel />
      <RightPanel />
    </div>

    <DeepVeil />

    <DevToggle v-if="isDev" />
  </div>
</template>
