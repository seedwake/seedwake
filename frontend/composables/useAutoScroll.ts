// Auto-scroll a list container to its bottom when its item count changes,
// but only if the user is already at (or very near) the bottom. If the user
// has scrolled up to read history, their position is left alone unless
// forceOnChange is enabled.
// Also exposes `isOverflowing` so callers can conditionally style the edge fade
// (no overflow → no mask needed; short content stays fully solid).
//
// Optional `idleReturnMs`: when the user stops scrolling/pointing/touching for
// this duration, smooth-scroll back to the latest. Keeps panels tracking the
// newest without fighting the reader while they're still actively inspecting it.

import type { Ref } from "vue";

export function useAutoScroll(
  elRef: Ref<HTMLElement | null>,
  getCount: () => number,
  opts?: { smooth?: boolean; idleReturnMs?: number; forceOnChange?: boolean | (() => boolean) },
): { isOverflowing: Ref<boolean> } {
  const THRESHOLD = 96;
  const IDLE_MS = opts?.idleReturnMs ?? 0;
  const isOverflowing = ref(false);
  let initialScrollDone = false;
  let ro: ResizeObserver | null = null;
  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  let lastActivityAt = 0;
  let ignoreScrollUntil = 0;

  function isAtBottom(node: HTMLElement): boolean {
    return node.scrollHeight - node.clientHeight - node.scrollTop <= THRESHOLD;
  }

  function shouldForceOnChange(): boolean {
    const force = opts?.forceOnChange;
    return typeof force === "function" ? force() : force === true;
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
    const elapsed = Date.now() - lastActivityAt;
    const delay = Math.max(IDLE_MS - elapsed, 0);
    idleTimer = setTimeout(() => {
      idleTimer = null;
      const node = elRef.value;
      if (!node || isAtBottom(node)) return;
      if (Date.now() - lastActivityAt < IDLE_MS) {
        scheduleIdle();
        return;
      }
      scrollToBottom(node);
    }, delay);
  }

  function scrollToBottom(node: HTMLElement): void {
    ignoreScrollUntil = Date.now() + 1_200;
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
    // Smooth scroll can begin before late layout changes settle; one frame later
    // keeps the destination anchored to the real bottom without a visible jump.
    requestAnimationFrame(() => {
      if (!isAtBottom(node)) node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
    });
  }

  // Fires on any user activity; resets the idle countdown so the auto-return
  // only triggers after real disengagement. `scroll` covers scrollbar drags and
  // inertial scrolling that may not produce wheel/touch events.
  function onInteract(): void {
    if (Date.now() < ignoreScrollUntil) return;
    lastActivityAt = Date.now();
    scheduleIdle();
  }

  function onPointerLeave(): void {
    scheduleIdle();
  }

  function onWindowBlur(): void {
    scheduleIdle();
  }

  function onVisibilityChange(): void {
    if (document.hidden) scheduleIdle();
  }

  watch(getCount, async (next) => {
    const node = elRef.value;
    if (!node) return;
    const firstPopulation = !initialScrollDone && next > 0;
    const stick = shouldForceOnChange() || isAtBottom(node);
    await nextTick();
    if (firstPopulation || stick) {
      // First population uses instant jump so a freshly loaded page lands at the
      // bottom without animating; incremental updates can opt into smooth via
      // `opts.smooth` so each new item glides into view.
      if (firstPopulation || !opts?.smooth) {
        node.scrollTop = node.scrollHeight;
      } else {
        scrollToBottom(node);
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
      lastActivityAt = Date.now();
      node.addEventListener("scroll", onInteract, { passive: true });
      node.addEventListener("wheel", onInteract, { passive: true });
      node.addEventListener("touchstart", onInteract, { passive: true });
      node.addEventListener("touchmove", onInteract, { passive: true });
      node.addEventListener("pointermove", onInteract, { passive: true });
      node.addEventListener("pointerleave", onPointerLeave);
      node.addEventListener("keydown", onInteract);
      window.addEventListener("blur", onWindowBlur);
      document.addEventListener("visibilitychange", onVisibilityChange);
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
      node.removeEventListener("scroll", onInteract);
      node.removeEventListener("wheel", onInteract);
      node.removeEventListener("touchstart", onInteract);
      node.removeEventListener("touchmove", onInteract);
      node.removeEventListener("pointermove", onInteract);
      node.removeEventListener("pointerleave", onPointerLeave);
      node.removeEventListener("keydown", onInteract);
      window.removeEventListener("blur", onWindowBlur);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    }
  });

  return { isOverflowing };
}
