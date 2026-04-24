// Auto-scroll a list container to its bottom when its item count changes,
// but only if the user is already at (or very near) the bottom. If the user
// has scrolled up to read history, their position is left alone.
// Also exposes `isOverflowing` so callers can conditionally style the edge fade
// (no overflow → no mask needed; short content stays fully solid).
//
// Optional `idleReturnMs`: when the user has scrolled away from the bottom and
// then disengages (mouse leaves the panel, no wheel/touch/keyboard activity)
// for this duration, smooth-scroll back to the latest. Keeps panels tracking
// the newest without fighting the reader while they're still focused on it.

import type { Ref } from "vue";

export function useAutoScroll(
  elRef: Ref<HTMLElement | null>,
  getCount: () => number,
  opts?: { smooth?: boolean; idleReturnMs?: number },
): { isOverflowing: Ref<boolean> } {
  const THRESHOLD = 48;
  const IDLE_MS = opts?.idleReturnMs ?? 0;
  const isOverflowing = ref(false);
  let initialScrollDone = false;
  let ro: ResizeObserver | null = null;
  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  let hovered = false;

  function isAtBottom(node: HTMLElement): boolean {
    return node.scrollHeight - node.clientHeight - node.scrollTop <= THRESHOLD;
  }

  function updateOverflow(): void {
    const node = elRef.value;
    if (node) isOverflowing.value = node.scrollHeight > node.clientHeight + 1;
  }

  function clearIdle(): void {
    if (idleTimer !== null) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
  }

  function scheduleIdle(): void {
    if (IDLE_MS <= 0) return;
    clearIdle();
    idleTimer = setTimeout(() => {
      idleTimer = null;
      const node = elRef.value;
      if (!node || hovered || isAtBottom(node)) return;
      node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
    }, IDLE_MS);
  }

  function onEnter(): void { hovered = true; clearIdle(); }
  function onLeave(): void { hovered = false; scheduleIdle(); }
  // Fires on any user-initiated scroll attempt; resets the idle countdown so
  // the auto-return only triggers after real disengagement.
  function onInteract(): void { scheduleIdle(); }

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
      if (firstPopulation) {
        initialScrollDone = true;
        // CJK serif/sans webfonts typically resolve after the DOM is populated;
        // when they do, glyphs reflow taller and scrollHeight grows past the
        // value we just scrolled to, leaving the last items clipped at the
        // bottom. Re-apply once fonts settle so the initial landing really is
        // the bottom. No-op for browsers without the Font Loading API.
        if (typeof document !== "undefined" && document.fonts) {
          void document.fonts.ready.then(() => {
            const n = elRef.value;
            if (n) n.scrollTop = n.scrollHeight;
          });
        }
      }
    }
    updateOverflow();
  }, { immediate: true });

  onMounted(() => {
    updateOverflow();
    const node = elRef.value;
    if (!node) return;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(updateOverflow);
      ro.observe(node);
    }
    if (IDLE_MS > 0) {
      node.addEventListener("mouseenter", onEnter);
      node.addEventListener("mouseleave", onLeave);
      node.addEventListener("wheel", onInteract, { passive: true });
      node.addEventListener("touchstart", onInteract, { passive: true });
      node.addEventListener("touchmove", onInteract, { passive: true });
      node.addEventListener("keydown", onInteract);
      // Start a pre-emptive timer so the return still fires if the user never
      // interacts at all — no-op when already at bottom.
      scheduleIdle();
    }
  });

  onBeforeUnmount(() => {
    ro?.disconnect();
    ro = null;
    clearIdle();
    const node = elRef.value;
    if (node && IDLE_MS > 0) {
      node.removeEventListener("mouseenter", onEnter);
      node.removeEventListener("mouseleave", onLeave);
      node.removeEventListener("wheel", onInteract);
      node.removeEventListener("touchstart", onInteract);
      node.removeEventListener("touchmove", onInteract);
      node.removeEventListener("keydown", onInteract);
    }
  });

  return { isOverflowing };
}
