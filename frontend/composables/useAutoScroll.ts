// Auto-scroll a list container to its bottom when its item count changes,
// but only if the user is already at (or very near) the bottom. If the user
// has scrolled up to read history, their position is left alone.
// Also exposes `isOverflowing` so callers can conditionally style the edge fade
// (no overflow → no mask needed; short content stays fully solid).

import type { Ref } from "vue";

export function useAutoScroll(
  elRef: Ref<HTMLElement | null>,
  getCount: () => number,
  opts?: { smooth?: boolean },
): { isOverflowing: Ref<boolean> } {
  const THRESHOLD = 48;
  const isOverflowing = ref(false);
  let initialScrollDone = false;
  let ro: ResizeObserver | null = null;

  function isAtBottom(node: HTMLElement): boolean {
    return node.scrollHeight - node.clientHeight - node.scrollTop <= THRESHOLD;
  }

  function updateOverflow(): void {
    const node = elRef.value;
    if (node) isOverflowing.value = node.scrollHeight > node.clientHeight + 1;
  }

  watch(getCount, async (next) => {
    const node = elRef.value;
    if (!node) return;
    const firstPopulation = !initialScrollDone && next > 0;
    const stick = isAtBottom(node);
    await nextTick();
    if (firstPopulation || stick) {
      // First population uses instant jump so a freshly loaded page lands at the
      // bottom without animating; incremental updates can opt into smooth via
      // `opts.smooth` so each new item glides into view.
      if (firstPopulation || !opts?.smooth) {
        node.scrollTop = node.scrollHeight;
      } else {
        node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
      }
      if (firstPopulation) initialScrollDone = true;
    }
    updateOverflow();
  }, { immediate: true });

  onMounted(() => {
    updateOverflow();
    const node = elRef.value;
    if (node && typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(updateOverflow);
      ro.observe(node);
    }
  });

  onBeforeUnmount(() => {
    ro?.disconnect();
    ro = null;
  });

  return { isOverflowing };
}
