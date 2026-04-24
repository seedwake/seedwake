<script setup lang="ts">
const props = defineProps<{
  cycleId: number;
  timestamp?: string;
}>();

const displayTime = computed(() => {
  if (!props.timestamp) return "";
  try {
    const d = new Date(props.timestamp);
    return d.toLocaleTimeString("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return "";
  }
});
</script>

<template>
  <div class="cycle-sep">
    <div>C{{ cycleId }}</div>
    <div class="line" />
    <div class="id">{{ displayTime }}</div>
  </div>
</template>
