<script setup lang="ts">
import type { RuntimeMode } from "~/types/api";

const props = defineProps<{
  mode: RuntimeMode;
  cycle: number;
}>();

const { t } = useI18n();

const label = computed(() => t(`mode.${props.mode}`));
const labelEn = computed(() => t(`mode.${props.mode}_en`));
</script>

<template>
  <div class="mode">
    <span class="dot" />
    <div class="label">
      {{ label }}
      <small>{{ labelEn }} · CYCLE {{ cycle }}</small>
    </div>
  </div>
</template>
