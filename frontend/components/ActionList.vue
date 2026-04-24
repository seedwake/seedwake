<script setup lang="ts">
import type { ActionItem } from "~/types/api";

const props = defineProps<{ actions: ActionItem[] }>();
const { t } = useI18n();
const resolve = useI18nText();

// Store keeps terminal actions so thought cards can show final status; in-flight
// panel only shows non-terminal (pending/running/awaiting_confirmation).
const TERMINAL = new Set(["succeeded", "failed", "timeout"]);
const pendingActions = computed(() =>
  props.actions.filter((a) => !TERMINAL.has(a.status)),
);

const panelRef = ref<HTMLElement | null>(null);
const { isOverflowing } = useAutoScroll(panelRef, () => pendingActions.value.length);

function stateKey(a: ActionItem): string {
  if (a.awaiting_confirmation) return "awaiting";
  const s = (a.status || "pending").toLowerCase();
  return ["pending", "running", "succeeded", "failed"].includes(s) ? s : "pending";
}

function stateLabel(a: ActionItem): string {
  return t(`action_state.${stateKey(a)}`);
}

const DETAIL_MAX_CHARS = 120;

// Prefer the planner's task statement (from request.task) so the panel reads
// like "reading RUNNING — 阅读并总结文章：…" instead of the redundant
// "reading RUNNING 执行中". Fall back to the i18n summary when task is absent.
function actionDetail(a: ActionItem): string {
  const task = (a.request?.task as string | undefined) || "";
  const firstLine = task.split(/\r?\n/).find((line) => line.trim()) || "";
  if (firstLine) {
    return firstLine.length > DETAIL_MAX_CHARS
      ? `${firstLine.slice(0, DETAIL_MAX_CHARS - 1).trimEnd()}…`
      : firstLine.trim();
  }
  return resolve(a.summary);
}
</script>

<template>
  <div class="panel">
    <div class="eyebrow">
      <span class="zh">{{ t("right.in_flight_label") }}</span>
      <span>{{ t("right.in_flight_label_en") }}</span>
    </div>
    <div class="scroll" ref="panelRef" :class="{ 'edge-fade': isOverflowing }">
      <p v-if="pendingActions.length === 0" class="msg">
        <span class="text" style="color: var(--ink-faint)">{{ t("right.empty_actions") }}</span>
      </p>
      <div
        v-for="a in pendingActions"
        :key="a.action_id"
        class="action-row"
        :data-state="stateKey(a)"
      >
        <div class="kind">{{ a.type }}</div>
        <div class="state"><span class="sd" /> {{ stateLabel(a) }}</div>
        <div class="detail">{{ actionDetail(a) }}</div>
      </div>
    </div>
  </div>
</template>
