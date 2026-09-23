"use client";

/**
 * My protocol, kept on this phone only (`localStorage`, key "brian.myProtocol").
 * The library's guided review, Treatments' "Add to protocol" and Find my
 * protocol all write here; Protocol reads it. No backend field exists for it.
 *
 * Every read is inside try/catch and answers EMPTY_PROTOCOL when storage is
 * missing, refused or holds something that is not a protocol. A write that
 * storage refuses still lasts for this page (an in-memory copy).
 */
import { useCallback, useSyncExternalStore } from "react";
import type { ProtocolItemTemplate, ProtocolTemplate } from "@/content/types";

export const MY_PROTOCOL_KEY = "brian.myProtocol";
/** Dispatched on `window` after every write, so open screens re-read. */
export const MY_PROTOCOL_EVENT = "brian:myprotocol";

export type MyProtocolSource = "template" | "treatment" | "find" | "manual";

export interface MyProtocolItem extends ProtocolItemTemplate {
  enabled: boolean;
  source: MyProtocolSource;
  /** ISO date. */
  addedAt: string;
}

export interface MyProtocol {
  templateId: string | null;
  templateName: string | null;
  items: MyProtocolItem[];
  flags: ProtocolTemplate["flags"] | null;
  /** ISO date, null until the first write. */
  updatedAt: string | null;
}

export const EMPTY_PROTOCOL: MyProtocol = {
  templateId: null,
  templateName: null,
  items: [],
  flags: null,
  updatedAt: null,
};

/** This page's last write, for a browser that refuses storage (private window, blocked site data). */
let memory: MyProtocol | null = null;

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;

/** Only what a protocol needs to render: everything else falls back to EMPTY. */
function normalize(value: unknown): MyProtocol {
  if (!isRecord(value) || !Array.isArray(value.items)) return EMPTY_PROTOCOL;
  const items = value.items.filter(
    (item): item is MyProtocolItem =>
      isRecord(item) && typeof item.id === "string" && typeof item.name === "string" && isRecord(item.window) && Array.isArray(item.days),
  );
  return {
    templateId: typeof value.templateId === "string" ? value.templateId : null,
    templateName: typeof value.templateName === "string" ? value.templateName : null,
    items: items.map((item) => ({ ...item, enabled: item.enabled !== false })),
    flags: isRecord(value.flags) ? (value.flags as MyProtocol["flags"]) : null,
    updatedAt: typeof value.updatedAt === "string" ? value.updatedAt : null,
  };
}

/** The last stored text and what it parsed to, so an unchanged store answers the same object (a stable snapshot). */
let cachedRaw: string | null = null;
let cachedProtocol: MyProtocol = EMPTY_PROTOCOL;

function parse(raw: string): MyProtocol {
  try {
    return normalize(JSON.parse(raw));
  } catch {
    return EMPTY_PROTOCOL;
  }
}

/**
 * The stored protocol, or EMPTY_PROTOCOL when there is none or storage is
 * refused. Client only. The same object comes back until the store changes,
 * so it serves as a snapshot; treat it as read-only.
 */
export function readMyProtocol(): MyProtocol {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(MY_PROTOCOL_KEY);
  } catch {
    // Storage refused: fall back to this page's own copy.
  }
  if (raw === null) return memory ?? EMPTY_PROTOCOL;
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cachedProtocol = parse(raw);
  }
  return cachedProtocol;
}

/** Stores the protocol and tells every open screen. Client only. */
export function writeMyProtocol(protocol: MyProtocol): void {
  memory = protocol;
  try {
    window.localStorage.setItem(MY_PROTOCOL_KEY, JSON.stringify(protocol));
  } catch {
    // Storage refused: the protocol lasts for this page.
  }
  try {
    window.dispatchEvent(new Event(MY_PROTOCOL_EVENT));
  } catch {
    // No window (a test without a DOM): nothing to notify.
  }
}

/**
 * Replaces the whole protocol with the template's enabled items and its flags.
 * Items left off in the review never land.
 */
export function adoptTemplate(template: ProtocolTemplate, enabledIds: string[], source: "template" | "find"): MyProtocol {
  const now = new Date().toISOString();
  const wanted = new Set(enabledIds);
  const protocol: MyProtocol = {
    templateId: template.id,
    templateName: template.name,
    items: template.items.filter((item) => wanted.has(item.id)).map((item) => ({ ...item, enabled: true, source, addedAt: now })),
    flags: { ...template.flags },
    updatedAt: now,
  };
  writeMyProtocol(protocol);
  return protocol;
}

/** Appends one item (a treatment, or one typed in). An item already there by id is turned on, not doubled. */
export function addItem(item: ProtocolItemTemplate, source: "treatment" | "manual"): MyProtocol {
  const now = new Date().toISOString();
  const current = readMyProtocol();
  const exists = current.items.some((row) => row.id === item.id);
  const items = exists
    ? current.items.map((row) => (row.id === item.id ? { ...row, ...item, enabled: true } : row))
    : [...current.items, { ...item, enabled: true, source, addedAt: now }];
  const protocol: MyProtocol = { ...current, items, updatedAt: now };
  writeMyProtocol(protocol);
  return protocol;
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(MY_PROTOCOL_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(MY_PROTOCOL_EVENT, onChange);
  };
}

const serverProtocol = (): MyProtocol => EMPTY_PROTOCOL;
const clientLoaded = (): boolean => true;
const serverLoaded = (): boolean => false;

/**
 * The stored protocol for a screen. The server render and the first client
 * render use EMPTY (`loaded` false), so hydration never mismatches; the store
 * is read right after, and again on every write on this page and on other
 * tabs. `refresh` forces a re-read.
 */
export function useMyProtocol(): { protocol: MyProtocol; loaded: boolean; refresh: () => void } {
  const protocol = useSyncExternalStore(subscribe, readMyProtocol, serverProtocol);
  const loaded = useSyncExternalStore(subscribe, clientLoaded, serverLoaded);
  const refresh = useCallback(() => {
    cachedRaw = null;
    window.dispatchEvent(new Event(MY_PROTOCOL_EVENT));
  }, []);
  return { protocol, loaded, refresh };
}
