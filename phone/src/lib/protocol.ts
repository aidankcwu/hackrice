/**
 * What Protocol shows. Two feeds meet here: the backend's `/api/protocol/today`
 * (the backend decides every status; the phone names it) and the local
 * My protocol (`myProtocol.ts`), whose status for today the phone works out
 * from the seeded month's events (`statusFor`) and a "done" map the wearer
 * writes (`localStorage` key "brian.protocolDone").
 */
import { useCallback, useSyncExternalStore } from "react";
import type { Anchored, ItemKind, Weekday, Window } from "@/content/types";
import type { Day, MonthEvent } from "./month/types";
import type { MyProtocolItem } from "./myProtocol";
import { WINDOWS, clock, hm } from "./rules";
import type { Family } from "./today";
import type { ProtocolKind, ProtocolStatus, ProtocolTodayItem } from "./types";

// ---------------------------------------------------------------------------
// Backend rows
// ---------------------------------------------------------------------------

/** The four backend kinds, in the order Add item lists them; each wears its family's symbol. */
export const KINDS: readonly { kind: ProtocolKind; label: string; family: Family }[] = [
  { kind: "dose", label: "Dose", family: "medication" },
  { kind: "meal", label: "Meal", family: "food" },
  { kind: "winddown", label: "Wind‑down", family: "winddown" },
  { kind: "walk", label: "Walk", family: "walk" },
];

export function kindFamily(kind: ProtocolKind): Family {
  return KINDS.find((entry) => entry.kind === kind)?.family ?? "medication";
}

/** "08:42" → "8:42": times of day are written without a leading zero. */
function hourMinute(text: string): string {
  return text.replace(/^0(?=\d)/, "");
}

const CLOCK = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });

/** "07:00 to 10:00", from the backend's local `"HH:MM"` pair. */
export function windowText(item: Pick<ProtocolTodayItem, "window_start" | "window_end">): string {
  return `${item.window_start} to ${item.window_end}`;
}

/**
 * "Seen 8:42" · "Waiting" · "Missed" · "Done". An item the wearer undid is
 * open again and reads "Waiting" until the backend says otherwise.
 */
export function statusText(item: Pick<ProtocolTodayItem, "status" | "seen_t">): string {
  switch (item.status) {
    case "seen":
      return item.seen_t != null ? `Seen ${hourMinute(CLOCK.format(new Date(item.seen_t * 1000)))}` : "Seen";
    case "done":
      return "Done";
    case "missed":
      return "Missed";
    default:
      return "Waiting";
  }
}

export type RowAction = "done" | "undo" | "delete";

/** The sheet's first choice: Undo on seen or done, Mark done on waiting or missed. Delete is always second. */
export function toggleAction(status: ProtocolStatus): "done" | "undo" {
  return status === "seen" || status === "done" ? "undo" : "done";
}

export const ACTION_LABELS: Record<RowAction, string> = {
  done: "Mark done",
  undo: "Undo",
  delete: "Delete",
};

/** Weekday toggles for Add item, 0 = Monday as the backend counts. */
export const WEEKDAYS: readonly { day: number; letter: string; name: string; short: string }[] = [
  { day: 0, letter: "M", name: "Monday", short: "Mon" },
  { day: 1, letter: "T", name: "Tuesday", short: "Tue" },
  { day: 2, letter: "W", name: "Wednesday", short: "Wed" },
  { day: 3, letter: "T", name: "Thursday", short: "Thu" },
  { day: 4, letter: "F", name: "Friday", short: "Fri" },
  { day: 5, letter: "S", name: "Saturday", short: "Sat" },
  { day: 6, letter: "S", name: "Sunday", short: "Sun" },
];

// ---------------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------------

export type Section = "doses" | "meals" | "movement" | "light" | "sleep" | "screens";

/** The screen's sections, in order. Daily amounts follow them. */
export const SECTIONS: readonly { id: Section; title: string }[] = [
  { id: "doses", title: "Doses" },
  { id: "meals", title: "Meals" },
  { id: "movement", title: "Movement" },
  { id: "light", title: "Light" },
  { id: "sleep", title: "Sleep" },
  { id: "screens", title: "Screens" },
];

/** A backend kind's section. */
export function backendSection(kind: ProtocolKind): Section {
  switch (kind) {
    case "meal":
      return "meals";
    case "winddown":
      return "sleep";
    case "walk":
      return "movement";
    default:
      return "doses";
  }
}

/** A local kind's section; `null` for hydration, which Daily amounts already shows as water. */
export function localSection(kind: ItemKind): Section | null {
  switch (kind) {
    case "dose":
    case "supplement":
      return "doses";
    case "meal":
      return "meals";
    case "move":
    case "sauna":
      return "movement";
    case "light":
      return "light";
    case "sleep":
      return "sleep";
    case "screens":
      return "screens";
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Windows and days, in words
// ---------------------------------------------------------------------------

/** "07:00" → 420. */
export function minutesOf(text: string): number {
  const [h, m] = text.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

export function isAnchored(window: Window | Anchored): window is Anchored {
  return "anchor" in window;
}

/** "07:00 to 10:00" · "2 h after waking, 30 min" · "1 h before bed, 20 min". */
export function localWindowText(window: Window | Anchored): string {
  if (!isAnchored(window)) return `${window.start} to ${window.end}`;
  const at = window.anchor === "wake" ? "waking" : "bed";
  const offset =
    window.offsetMin === 0 ? `At ${at}` : window.offsetMin > 0 ? `${hm(window.offsetMin)} after ${at}` : `${hm(-window.offsetMin)} before ${at}`;
  return `${offset}, ${hm(window.lengthMin)}`;
}

/**
 * A local window as minutes after midnight for one day. A wake anchor reads the
 * day's sleep record; a bed anchor uses tonight's bed target, since tonight has
 * not happened yet.
 */
export function resolveWindow(window: Window | Anchored, day?: Pick<Day, "sleep">): { start: number; end: number } {
  if (!isAnchored(window)) return { start: minutesOf(window.start), end: minutesOf(window.end) };
  const anchor = window.anchor === "wake" ? (day?.sleep.wake ?? WINDOWS.wake) : WINDOWS.sleepStart;
  const start = anchor + window.offsetMin;
  return { start, end: start + window.lengthMin };
}

/** "Every day" · "Weekdays" · "Weekends" · "Mon, Wed, Fri". */
export function daysText(days: readonly number[]): string {
  const set = [...new Set(days)].sort((a, b) => a - b);
  if (set.length === 7) return "Every day";
  if (set.length === 5 && set.every((d) => d <= 4)) return "Weekdays";
  if (set.length === 2 && set[0] === 5 && set[1] === 6) return "Weekends";
  return set.map((d) => WEEKDAYS[d]?.short ?? "").filter(Boolean).join(", ");
}

/** Monday = 0, as the backend and the templates count, for an ISO date. */
export function weekdayOf(date: string): Weekday {
  const [y, m, d] = date.split("-").map(Number);
  const js = new Date(y, (m || 1) - 1, d || 1).getDay();
  return ((js + 6) % 7) as Weekday;
}

// ---------------------------------------------------------------------------
// Local status for today
// ---------------------------------------------------------------------------

export interface LocalStatus {
  status: "seen" | "waiting" | "missed" | "done";
  /** Minutes after midnight the item was seen. */
  time?: number;
}

const isSpan = (event: MonthEvent): event is MonthEvent & { minutes: number } => "minutes" in event;

/** The event overlaps the window: a point inside it, or a span crossing it. */
function inside(event: MonthEvent, start: number, end: number): boolean {
  const from = event.start;
  const to = isSpan(event) ? event.start + event.minutes : event.start;
  return from <= end && to >= start;
}

/** The event that satisfies the item, by kind. */
function matches(item: Pick<MyProtocolItem, "kind">, event: MonthEvent, start: number, end: number): boolean {
  if (!inside(event, start, end)) return false;
  switch (item.kind) {
    case "dose":
      return (event.kind === "peptide" && event.taken) || event.kind === "supplements";
    case "supplement":
      return event.kind === "supplements";
    case "meal":
      return event.kind === "meal";
    case "move":
      return event.kind === "workout";
    case "light":
      return event.kind === "outdoor" && event.sunlight;
    case "sauna":
      return event.kind === "sauna" || event.kind === "cold";
    default:
      return false;
  }
}

/** Minutes after midnight on one clock: a bed before midnight, or a window edge past it, wraps into 0 to 1439. */
function normalized(minutes: number): number {
  return ((minutes % 1440) + 1440) % 1440;
}

/** A bed this long before lights out still meets a bed window; only a late bed breaks it. */
const EARLY_BED_SLACK = 180;

/**
 * An item's status for `day`, read up to minute `until`: "seen" when an event
 * of its kind fell inside the window (dose ↔ a taken peptide or supplements;
 * supplement ↔ supplements; meal ↔ meal; move ↔ workout; light ↔ outdoor with
 * sunlight; sauna ↔ sauna or cold; sleep ↔ last night's bed inside the window,
 * an early bed included; screens ↔ no screen running past the window's start),
 * "waiting" while the window is open or ahead, "missed"
 * once it has closed with nothing seen. "done" comes from the done map, not
 * from here.
 */
export function statusFor(
  item: Pick<MyProtocolItem, "kind" | "window">,
  day: Pick<Day, "sleep" | "events">,
  until: number,
): LocalStatus {
  const { start, end } = resolveWindow(item.window, day);

  if (item.kind === "sleep") {
    // Last night's record is complete. A bed anchor is the record's own bed, so it is met by definition.
    const bed = normalized(day.sleep.bed);
    if (isAnchored(item.window) && item.window.anchor === "bed") return { status: "seen", time: bed };
    // The window may cross midnight (22:30 to 06:30, or a bed anchor's 22:30 plus 8 h); an early bed still meets it.
    const s = normalized(start);
    const e = normalized(end);
    const wraps = e < s;
    const met = wraps ? (bed >= s - EARLY_BED_SLACK && bed <= 1439) || bed <= e : bed >= s - EARLY_BED_SLACK && bed <= e;
    return met ? { status: "seen", time: bed } : { status: "missed" };
  }

  if (item.kind === "screens") {
    // Any screen running past the window's start breaks it, whether it began before or after.
    const broken = day.events.find((event) => (event.kind === "screen" || event.kind === "phone_in_bed") && event.start + event.minutes > start);
    if (broken) return { status: "missed" };
    return until >= end ? { status: "seen" } : { status: "waiting" };
  }

  const seen = day.events.filter((event) => matches(item, event, start, end)).sort((a, b) => a.start - b.start)[0];
  if (seen) return { status: "seen", time: seen.start };
  return until < end ? { status: "waiting" } : { status: "missed" };
}

/** "Seen 8:42" · "Waiting" · "Missed" · "Done". */
export function localStatusText(status: LocalStatus): string {
  switch (status.status) {
    case "seen":
      return status.time != null ? `Seen ${clock(status.time)}` : "Seen";
    case "done":
      return "Done";
    case "missed":
      return "Missed";
    default:
      return "Waiting";
  }
}

// ---------------------------------------------------------------------------
// The done map: what the wearer marked, per item and day
// ---------------------------------------------------------------------------

export const DONE_KEY = "brian.protocolDone";
const DONE_EVENT = "brian:protocolDone";

export type DoneMap = Record<string, true>;

/** `<item id>|<ISO date>`. */
export function doneKey(id: string, date: string): string {
  return `${id}|${date}`;
}

let memoryDone: DoneMap | null = null;
let cachedDoneRaw: string | null = null;
let cachedDone: DoneMap = {};

function parseDone(raw: string): DoneMap {
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null || Array.isArray(value)) return {};
    const map: DoneMap = {};
    for (const [key, on] of Object.entries(value)) if (on === true) map[key] = true;
    return map;
  } catch {
    return {};
  }
}

/** The done map, or an empty one when storage is refused. Client only; a stable snapshot until it changes. */
export function readDone(): DoneMap {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(DONE_KEY);
  } catch {
    // Storage refused: fall back to this page's own copy.
  }
  if (raw === null) return memoryDone ?? cachedDone;
  if (raw !== cachedDoneRaw) {
    cachedDoneRaw = raw;
    cachedDone = parseDone(raw);
  }
  return cachedDone;
}

function writeDone(map: DoneMap): void {
  memoryDone = map;
  cachedDone = map;
  try {
    const raw = JSON.stringify(map);
    cachedDoneRaw = raw;
    window.localStorage.setItem(DONE_KEY, raw);
  } catch {
    // Storage refused: the map lasts for this page.
  }
  try {
    window.dispatchEvent(new Event(DONE_EVENT));
  } catch {
    // No window: nothing to notify.
  }
}

/** Marks or unmarks one item for one day. */
export function setDone(id: string, date: string, on: boolean): void {
  const key = doneKey(id, date);
  const current = readDone();
  if (Boolean(current[key]) === on) return;
  const next: DoneMap = { ...current };
  if (on) next[key] = true;
  else delete next[key];
  writeDone(next);
}

function subscribeDone(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(DONE_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(DONE_EVENT, onChange);
  };
}

const EMPTY_DONE: DoneMap = {};
const serverDone = (): DoneMap => EMPTY_DONE;

/** The done map for a screen, re-read on every write. The server render sees an empty map. */
export function useDoneMap(): { done: DoneMap; isDone: (id: string, date: string) => boolean; mark: (id: string, date: string, on: boolean) => void } {
  const done = useSyncExternalStore(subscribeDone, readDone, serverDone);
  const isDone = useCallback((id: string, date: string) => Boolean(done[doneKey(id, date)]), [done]);
  const mark = useCallback((id: string, date: string, on: boolean) => setDone(id, date, on), []);
  return { done, isDone, mark };
}
