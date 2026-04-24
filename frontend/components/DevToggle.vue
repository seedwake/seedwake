<script setup lang="ts">
import type { RuntimeMode } from "~/types/api";

const { t } = useI18n();
const store = useSeedwakeState();

const modes: RuntimeMode[] = ["waking", "light_sleep", "deep_sleep"];

function pick(m: RuntimeMode) {
  store.setMode(m);
}
</script>

<template>
  <div class="devbar">
    <div class="dev-label">{{ t("dev.label") }}</div>
    <button
      v-for="m in modes"
      :key="m"
      :class="{ active: store.mode.value === m }"
      @click="pick(m)"
    >
      {{ t(`mode.${m}`) }}
    </button>
  </div>
</template>
