<script setup lang="ts">
import type { SerializedThought, ThoughtType } from "~/types/api";

const props = defineProps<{
  thought: SerializedThought;
  attended: boolean;
  visualIndex: number;
  actionStatus?: { state: string; summary: string };
}>();

const { t } = useI18n();

const canonicalType = computed<ThoughtType | string>(() => {
  const raw = (props.thought.type || "").toLowerCase();
  const allowed: ThoughtType[] = ["thinking", "intention", "reaction", "reflection"];
  return (allowed as string[]).includes(raw) ? (raw as ThoughtType) : "thinking";
});

const zhLabel = computed(() => t(`thought_type.${canonicalType.value}`));
const enLabel = computed(() => t(`thought_type_en.${canonicalType.value}`));

const cycleTag = computed(
  () => `C${props.thought.cycle_id}-${props.thought.index}`,
);

// Trailing parenthesized reference like "(← C1663-3)" that the backend parser
// sometimes leaves inline instead of hoisting into trigger_ref. Strip it from the
// body and surface it through the same structural slot.
const TRAILING_TRIGGER_RE = /\s*[(（]\s*←\s*(C\d+-\d+)\s*[)）]\s*$/;

const extractedTriggerRef = computed<string | null>(() => {
  const match = (props.thought.content || "").match(TRAILING_TRIGGER_RE);
  return match ? (match[1] ?? null) : null;
});

const triggerRef = computed(() => props.thought.trigger_ref || extractedTriggerRef.value);

// Visual opacity data-vi is capped at 5 for the "full" position.
const vi = computed(() => Math.min(5, Math.max(0, props.visualIndex)));

const actionKind = computed(() => props.thought.action_request?.type || null);

// Clean the displayed body:
//  - strip the trailing `{action:..., content:"..."}` DSL block (rendered via the
//    action chip instead);
//  - strip a trailing parenthesized trigger ref like "(← C1663-3)" that the
//    backend parser missed (hoisted into the .trigger slot via triggerRef above).
const displayContent = computed(() => {
  const raw = props.thought.content || "";
  let cleaned = raw.trimEnd();
  if (cleaned.endsWith("}")) {
    const idx = cleaned.lastIndexOf("{action:");
    if (idx >= 0) cleaned = cleaned.slice(0, idx).trimEnd();
  }
  cleaned = cleaned.replace(TRAILING_TRIGGER_RE, "");
  return cleaned;
});
</script>

<template>
  <article
    class="thought"
    :class="{ attended }"
    :data-type="canonicalType"
    :data-vi="vi"
  >
    <div class="gutter">
      <div class="cid">{{ cycleTag }}</div>
    </div>
    <div>
      <div class="tag">
        <span class="zh">{{ zhLabel }}</span>
        <span>· {{ enLabel }}</span>
        <span v-if="attended"> · {{ t("attention.attended") }}</span>
      </div>
      <p class="body">{{ displayContent }}</p>
      <div v-if="triggerRef" class="trigger">
        <span class="arrow">←</span>{{ triggerRef }}
      </div>
      <!-- Only show the chip when we actually know the action's state.
           If actionStatus is undefined the planner declined the request (no
           ActionRecord was created in Redis); chip stayed "pending" before,
           which read as "action stuck" — misleading. Hiding is cleaner. -->
      <div
        v-if="actionKind && actionStatus"
        class="action-chip"
        :data-state="actionStatus.state"
      >
        <span class="state-dot" />
        <span class="kind">{{ actionKind }}</span>
        <span class="state">
          {{ t(`action_state.${actionStatus.state}`) }}
        </span>
      </div>
    </div>
  </article>
</template>
