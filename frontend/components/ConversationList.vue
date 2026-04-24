<script setup lang="ts">
import type { ConversationEntry } from "~/types/api";

const props = defineProps<{ entries: ConversationEntry[] }>();
const { t } = useI18n();

const panelRef = ref<HTMLElement | null>(null);
const { isOverflowing } = useAutoScroll(
  panelRef,
  () => props.entries.length,
  { smooth: true, idleReturnMs: 12000 },
);

function isSelf(entry: ConversationEntry): boolean {
  if (entry.direction === "outbound") return true;
  const role = (entry.role || "").toLowerCase();
  return role === "assistant" || role === "self";
}

function speakerName(entry: ConversationEntry): string {
  if (isSelf(entry)) return t("right.self_name");
  return (entry.speaker_name || entry.username || entry.full_name || entry.source || "").toUpperCase();
}

function displayTime(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {
    return "";
  }
}
</script>

<template>
  <div class="panel">
    <div class="eyebrow">
      <span class="zh">{{ t("right.conversation_label") }}</span>
      <span>{{ t("right.conversation_label_en") }}</span>
    </div>
    <div class="scroll" ref="panelRef" :class="{ 'edge-fade': isOverflowing }">
      <template v-if="entries.length === 0">
        <p class="msg"><span class="text" style="color: var(--ink-faint)">{{ t("right.empty_conversation") }}</span></p>
      </template>
      <div
        v-for="entry in entries"
        :key="entry.entry_id"
        class="msg"
        :class="{ inbound: !isSelf(entry) }"
      >
        <div class="who">
          <span class="name" :class="{ self: isSelf(entry) }">{{ speakerName(entry) }}</span>
          <span class="t">{{ displayTime(entry.timestamp) }}</span>
        </div>
        <p class="text">{{ entry.content }}</p>
      </div>
    </div>
  </div>
</template>
