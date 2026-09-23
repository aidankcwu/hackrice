"use client";

/**
 * Which devices this phone counts as connected, kept on this phone only
 * (`localStorage`). No pairing flow exists yet: the state is a placeholder the
 * wearer can flip on a device's detail, seeded from the content (only the
 * glasses start connected) until this phone stores its own list.
 *
 * Read through `useSyncExternalStore`: the server render and hydration use the
 * seeded state, then the stored one, so the markup never mismatches. (A read
 * inside an effect would set state there, which this repo's lint forbids.)
 */
import { useCallback, useSyncExternalStore } from "react";
import { DEVICES } from "@/content/devices";

/** A JSON array of device ids. Absent: the seeded state. */
export const DEVICES_KEY = "brian.devices";
/** Fired on this page after every write, so every open screen re-reads. */
const CHANGED = "brian:devices";

/** The seeded connect state, from the content. */
export const SEEDED_CONNECTED: readonly string[] = DEVICES.filter((device) => device.connected).map((device) => device.id);
const SEEDED_SET: ReadonlySet<string> = new Set(SEEDED_CONNECTED);

/** This page's own writes, for a browser that refuses storage (private window, blocked site data). */
let memory: string | null = null;

/** The stored JSON, else what this page wrote, else null (seeded). Never throws. */
function rawValue(): string | null {
  try {
    const stored = window.localStorage.getItem(DEVICES_KEY);
    if (stored !== null) return stored;
  } catch {
    // Storage refused: fall back to what this page set.
  }
  return memory;
}

function parse(raw: string | null): string[] {
  if (raw === null) return [...SEEDED_CONNECTED];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed.filter((id): id is string => typeof id === "string");
  } catch {
    // Unreadable: seeded.
  }
  return [...SEEDED_CONNECTED];
}

/** The connected ids: stored, else what this page wrote, else seeded. Never throws. */
export function readConnectedIds(): string[] {
  return parse(rawValue());
}

export function writeConnectedIds(ids: readonly string[]): void {
  const raw = JSON.stringify(ids);
  memory = raw;
  try {
    window.localStorage.setItem(DEVICES_KEY, raw);
  } catch {
    // Storage refused: the choice lasts for this page.
  }
  window.dispatchEvent(new Event(CHANGED));
}

/** Connects or disconnects one device, writes, and returns the new list. */
export function setDeviceConnected(id: string, on: boolean): string[] {
  const rest = readConnectedIds().filter((other) => other !== id);
  const next = on ? [...rest, id] : rest;
  writeConnectedIds(next);
  return next;
}

/** The last snapshot, keyed on the raw value, so an unchanged store returns the same set. */
let cache: { raw: string | null; ids: ReadonlySet<string> } | null = null;

function snapshot(): ReadonlySet<string> {
  const raw = rawValue();
  if (cache && cache.raw === raw) return cache.ids;
  cache = { raw, ids: new Set(parse(raw)) };
  return cache.ids;
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGED, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGED, onChange);
  };
}

export interface DevicesState {
  /** Ids of the connected devices. Seeded on the server render and during hydration. */
  connected: ReadonlySet<string>;
  setConnected: (id: string, on: boolean) => void;
}

/** The connect state, live: it follows every write, here or in another tab. */
export function useDevices(): DevicesState {
  const connected = useSyncExternalStore(subscribe, snapshot, () => SEEDED_SET);
  const setConnected = useCallback((id: string, on: boolean) => {
    setDeviceConnected(id, on);
  }, []);
  return { connected, setConnected };
}
