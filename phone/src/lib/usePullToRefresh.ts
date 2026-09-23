"use client";

import { useEffect, useRef, useState } from "react";

/** Pull distance (px, after resistance) that triggers a refresh, and the most the page follows the finger. */
export const PULL_THRESHOLD = 64;
const PULL_MAX = 96;

/**
 * Pull to refresh on touch screens: a downward drag that starts with the page at
 * the top. `pull` is how far the content follows the finger; `busy` is true while
 * the refresh it triggered runs.
 */
export function usePullToRefresh(onRefresh: () => Promise<void>): { pull: number; busy: boolean } {
  const [pull, setPull] = useState(0);
  const [busy, setBusy] = useState(false);
  const refresh = useRef(onRefresh);

  useEffect(() => {
    refresh.current = onRefresh;
  }, [onRefresh]);

  useEffect(() => {
    let startY: number | null = null;
    let distance = 0;
    const start = (event: TouchEvent) => {
      startY = window.scrollY <= 0 && event.touches.length === 1 ? event.touches[0].clientY : null;
      distance = 0;
    };
    const move = (event: TouchEvent) => {
      if (startY === null) return;
      distance = Math.max(0, event.touches[0].clientY - startY) / 2;
      setPull(Math.min(distance, PULL_MAX));
    };
    const end = () => {
      if (startY === null) return;
      startY = null;
      setPull(0);
      if (distance < PULL_THRESHOLD) return;
      setBusy(true);
      void refresh.current().finally(() => setBusy(false));
    };
    window.addEventListener("touchstart", start, { passive: true });
    window.addEventListener("touchmove", move, { passive: true });
    window.addEventListener("touchend", end);
    window.addEventListener("touchcancel", end);
    return () => {
      window.removeEventListener("touchstart", start);
      window.removeEventListener("touchmove", move);
      window.removeEventListener("touchend", end);
      window.removeEventListener("touchcancel", end);
    };
  }, []);

  return { pull, busy };
}
