<script setup lang="ts">
const { t } = useI18n();
</script>

<template>
  <div class="deep-veil">
    <div class="deep-center">
      <div class="seal"><span class="glyph">{{ t("deep.seal_glyph") }}</span></div>
      <div class="deep-label">
        {{ t("deep.title") }}
        <small>{{ t("deep.subtitle") }}</small>
      </div>
    </div>
  </div>
</template>
