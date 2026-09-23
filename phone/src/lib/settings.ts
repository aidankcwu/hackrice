"use client";

/**
 * The phone's own settings, kept on this phone only (`localStorage`). None of
 * them reaches the backend: the persona route (`PUT /api/persona`) takes free
 * text and no wind-down field, so the wind-down time stays here, and Settings
 * says so ("Not connected yet").
 */
import { useCallback, useSyncExternalStore } from "react";

/** "on" | "off". Off: whispers become a notification, never both (IOS_SPEC "Whispers and notifications"). */
export const VOICE_KEY = "brian.voice";
/** "HH:MM", local time. */
export const WIND_DOWN_KEY = "brian.windDown";
/** The seeded Wind‑down item's window start (docs/API.md "The protocol": 21:30–23:00). */
export const WIND_DOWN_DEFAULT = "21:30";

const CHANGED = "brian-setting";
/** This page's writes, for a browser that refuses storage (private window, blocked site data). */
const memory = new Map<string, string>();

function read(key: string): string | null {
  try {
    const stored = window.localStorage.getItem(key);
    if (stored !== null) return stored;
  } catch {
    // Storage refused: fall back to what this page set.
  }
  return memory.get(key) ?? null;
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGED, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGED, onChange);
  };
}

/**
 * One stored setting as `[value, set]`. The server render and the first client
 * render use `fallback`, then the stored value, so hydration never mismatches.
 */
export function useSetting(key: string, fallback: string): [string, (value: string) => void] {
  const value = useSyncExternalStore(
    subscribe,
    () => read(key) ?? fallback,
    () => fallback,
  );
  const set = useCallback(
    (next: string) => {
      memory.set(key, next);
      try {
        window.localStorage.setItem(key, next);
      } catch {
        // Storage refused: the choice lasts for this page.
      }
      window.dispatchEvent(new Event(CHANGED));
    },
    [key],
  );
  return [value, set];
}
