<script setup lang="ts">
const { t } = useI18n();
const store = useSeedwakeState();
</script>

<template>
  <aside class="col right">
    <div class="section-title">
      <span class="zh-big">{{ t("section.others") }}</span>
      <span class="en">{{ t("section.others_en") }}</span>
    </div>

    <ConversationList :entries="store.conversation.value" />
    <div class="section-divider" />
    <ActionList :actions="store.actions.value" />
    <div class="section-divider" />
    <StimulusList :stimuli="store.stimuli.value" />
  </aside>
</template>
