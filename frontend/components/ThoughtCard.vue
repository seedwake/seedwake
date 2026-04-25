<script setup lang="ts">
import type { SerializedThought, ThoughtType } from "~/types/api";

const props = defineProps<{
  thought: SerializedThought;
  attended: boolean;
  activeAttended: boolean;
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

// Planner LLM runs for ~30s between thought emission and the first `pending`
// SSE event, which is after drip-feed already revealed the thought. Without a
// placeholder the chip stays hidden through that window and only lights up at
// the terminal event — the user sees it "pop in as COMPLETED" out of nowhere.
// Treat a thought with a raw action_request but no backend-confirmed status
// as implicit pending; it'll resolve to running/succeeded/failed as events
// arrive (planner-declined now surfaces as a `failed` event).
const chipState = computed(() => props.actionStatus?.state || "pending");

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
    :class="{ attended: activeAttended }"
    :data-type="canonicalType"
    :data-vi="vi"
  >
    <div class="gutter">
      <div class="cid">{{ cycleTag }}</div>
    </div>
    <div>
      <div class="tag">
        <!-- Bilingual separator dot lives inside the .zh span so EN mode (where
             .zh is hidden) drops both the CN label and the leading dot together. -->
        <span class="zh">{{ zhLabel }} · </span>
        <span>{{ enLabel }}</span>
        <span v-if="attended"> · {{ t("attention.attended") }}</span>
      </div>
      <p class="body">{{ displayContent }}</p>
      <div v-if="triggerRef" class="trigger">
        <span class="arrow">←</span>{{ triggerRef }}
      </div>
      <!-- Show the chip whenever the thought has an action_request. Default
           state is 'pending' until a backend event confirms a different state;
           codex persists planner-declined actions as `failed`, so the chip
           won't get stuck pending if the planner ultimately refuses. -->
      <div
        v-if="actionKind"
        class="action-chip"
        :data-state="chipState"
      >
        <span class="state-dot" />
        <span class="kind">{{ actionKind }}</span>
        <span class="state">
          {{ t(`action_state.${chipState}`) }}
        </span>
      </div>
    </div>
  </article>
</template>
