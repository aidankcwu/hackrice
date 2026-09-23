"use client";

/**
 * The concierge's settings, kept on this phone only (`localStorage`
 * "brian.concierge"). No backend field exists for any of them, the persona
 * included, so the store is the whole truth. The server render and hydration
 * use `DEFAULT_CONCIERGE`; the stored values land right after, through
 * `useSyncExternalStore`, so nothing reads storage during a server render.
 */
import { useCallback, useSyncExternalStore } from "react";
import { DEFAULT_CONCIERGE } from "@/content/concierge";
import type { ConciergeSettings, Talkativeness } from "@/content/types";

export const CONCIERGE_KEY = "brian.concierge";
/** Dispatched on `window` after every write, so every open hook re-reads. */
export const CONCIERGE_CHANGED = "brian:concierge";

const TALKATIVENESS_IDS: readonly Talkativeness[] = ["rare", "normal", "chatty"];
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Anything stored that is not the right shape falls back to the default for that field. */
function sanitize(input: unknown): ConciergeSettings {
  const raw = (typeof input === "object" && input !== null ? input : {}) as Record<string, unknown>;
  const talkativeness = TALKATIVENESS_IDS.includes(raw.talkativeness as Talkativeness)
    ? (raw.talkativeness as Talkativeness)
    : DEFAULT_CONCIERGE.talkativeness;
  const time = (value: unknown, fallback: string): string => (typeof value === "string" && TIME.test(value) ? value : fallback);
  const flag = (value: unknown, fallback: boolean): boolean => (typeof value === "boolean" ? value : fallback);
  return {
    talkativeness,
    quietStart: time(raw.quietStart, DEFAULT_CONCIERGE.quietStart),
    quietEnd: time(raw.quietEnd, DEFAULT_CONCIERGE.quietEnd),
    voiceOnGlasses: flag(raw.voiceOnGlasses, DEFAULT_CONCIERGE.voiceOnGlasses),
    mayAddWalk: flag(raw.mayAddWalk, DEFAULT_CONCIERGE.mayAddWalk),
    mayShieldApps: flag(raw.mayShieldApps, DEFAULT_CONCIERGE.mayShieldApps),
    persona: typeof raw.persona === "string" ? raw.persona : DEFAULT_CONCIERGE.persona,
  };
}

/** This page's last write, for a browser that refuses storage (private window, blocked site data). */
let memory: ConciergeSettings | null = null;
/** The last parsed value and the string it came from, so an unchanged store returns the same object. */
let cachedRaw: string | null = null;
let cached: ConciergeSettings = DEFAULT_CONCIERGE;

/**
 * The stored settings; failing that this page's last write; failing that the
 * defaults. Stable between calls while the store is unchanged. Treat the
 * result as read-only.
 */
export function readConcierge(): ConciergeSettings {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(CONCIERGE_KEY);
  } catch {
    return memory ?? DEFAULT_CONCIERGE;
  }
  if (raw === null) return memory ?? DEFAULT_CONCIERGE;
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    try {
      cached = sanitize(JSON.parse(raw));
    } catch {
      cached = memory ?? DEFAULT_CONCIERGE;
    }
  }
  return cached;
}

/** Writes the whole settings object and tells every open hook. A refused storage keeps the choice for this page only. */
export function writeConcierge(settings: ConciergeSettings): void {
  memory = settings;
  try {
    window.localStorage.setItem(CONCIERGE_KEY, JSON.stringify(settings));
  } catch {
    // Storage refused: `memory` carries the choice for this page.
  }
  window.dispatchEvent(new Event(CONCIERGE_CHANGED));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(CONCIERGE_CHANGED, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CONCIERGE_CHANGED, onChange);
  };
}

const serverSettings = (): ConciergeSettings => DEFAULT_CONCIERGE;
const clientLoaded = () => true;
const serverLoaded = () => false;

export interface UseConcierge {
  settings: ConciergeSettings;
  /** False on the server and during hydration, true once the stored values can show. */
  loaded: boolean;
  /** Merges a change in and writes it at once. */
  update: (patch: Partial<ConciergeSettings>) => void;
}

/**
 * The settings as state: defaults on the server and while hydrating, the
 * stored values after, and every later write from this or another tab
 * through the "storage" and "brian:concierge" events.
 */
export function useConcierge(): UseConcierge {
  const settings = useSyncExternalStore(subscribe, readConcierge, serverSettings);
  const loaded = useSyncExternalStore(subscribe, clientLoaded, serverLoaded);
  const update = useCallback((patch: Partial<ConciergeSettings>) => {
    writeConcierge({ ...readConcierge(), ...patch });
  }, []);
  return { settings, loaded, update };
}
