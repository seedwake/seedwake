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
const triggerRef = computed(() => props.thought.trigger_ref || null);

// Visual opacity data-vi is capped at 5 for the "full" position.
const vi = computed(() => Math.min(5, Math.max(0, props.visualIndex)));

const actionKind = computed(() => props.thought.action_request?.type || null);
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
      <p class="body">{{ thought.content }}</p>
      <div v-if="triggerRef" class="trigger">
        <span class="arrow">←</span>{{ triggerRef }}
      </div>
      <div
        v-if="actionKind"
        class="action-chip"
        :data-state="actionStatus?.state || 'pending'"
      >
        <span class="state-dot" />
        <span class="kind">{{ actionKind }}</span>
        <span class="state">
          {{ actionStatus?.summary || t(`action_state.${actionStatus?.state || 'pending'}`) }}
        </span>
      </div>
    </div>
  </article>
</template>
