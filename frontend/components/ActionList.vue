<script setup lang="ts">
import type { ActionItem } from "~/types/api";

defineProps<{ actions: ActionItem[] }>();
const { t } = useI18n();
const resolve = useI18nText();

function stateKey(a: ActionItem): string {
  if (a.awaiting_confirmation) return "awaiting";
  const s = (a.status || "pending").toLowerCase();
  return ["pending", "running", "succeeded", "failed"].includes(s) ? s : "pending";
}

function stateLabel(a: ActionItem): string {
  return t(`action_state.${stateKey(a)}`);
}
</script>

<template>
  <div class="panel">
    <div class="eyebrow">
      <span class="zh">{{ t("right.in_flight_label") }}</span>
      <span>{{ t("right.in_flight_label_en") }}</span>
    </div>
    <p v-if="actions.length === 0" class="msg">
      <span class="text" style="color: var(--ink-faint)">{{ t("right.empty_actions") }}</span>
    </p>
    <div
      v-for="a in actions"
      :key="a.action_id"
      class="action-row"
      :data-state="stateKey(a)"
    >
      <div class="kind">{{ a.type }}</div>
      <div class="state"><span class="sd" /> {{ stateLabel(a) }}</div>
      <div class="detail">{{ resolve(a.summary) }}</div>
    </div>
  </div>
</template>
