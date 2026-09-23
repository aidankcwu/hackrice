/**
 * Fixtures mode (`NEXT_PUBLIC_FIXTURES=1`): the web twin of the native app's
 * `-demo`. Two sets of the same files, keyed here by the route each one answers:
 *
 *   phone/fixtures/*.json         copies of `ios/Brian/Fixtures/`, captured from the
 *                                 backend in `--source sim` (docs/STATE.md §4). Never edited.
 *   phone/fixtures/design/*.json  the captures with the values the human set for the
 *                                 design screenshots: hours_today 1.4, overall 71,
 *                                 years_delta +0.3 (the same sign as the hours), the
 *                                 session started 42 min ago, and every measured
 *                                 provenance source "seeded".
 *
 * A screenshot URL (one that carries `?screen=`) reads the design set; any other
 * URL reads the captures. Loaded on first use, so a live build never downloads them.
 *
 *   (design) protocol_today.json   the capture with one item in each state the
 *                                 Protocol screen names: Morning dose seen at 8:42
 *                                 (IOS_SPEC's "Seen 8:42"), Daylight walk missed,
 *                                 Lunch window done by the wearer, the rest waiting.
 *
 * Two screenshot states change what the design set answers, by the `?screen=` suffix:
 *
 *   `-empty`  nothing seen yet today: no session, no episodes, no decisions, no
 *             protocol items, and the glasses app not connected. The hero keeps the
 *             design payload.
 *   `-error`  the backend never answers (`fixtureUnreachable()`).
 *
 * Protocol writes (`/done`, `/undo`, `DELETE`, `POST /api/protocol`) land on an
 * in-memory copy of `protocol_today.json` (`writeFixture`), so the action sheet and
 * Add item can be tried without a backend. A reload restores the capture.
 */
import type { NewProtocolItem, ProtocolItem, ProtocolToday, ProtocolTodayItem, Status } from "./types";

type Loader = () => Promise<{ default: unknown }>;
export type FixtureSet = "capture" | "design";

const SETS: Record<FixtureSet, Record<string, Loader>> = {
  capture: {
    "/api/status": () => import("../../fixtures/status.json"),
    "/api/healthspan": () => import("../../fixtures/today_healthspan.json"),
    "/api/episodes": () => import("../../fixtures/today_episodes.json"),
    "/api/decisions": () => import("../../fixtures/today_decisions.json"),
    "/api/protocol/today": () => import("../../fixtures/protocol_today.json"),
  },
  design: {
    "/api/status": () => import("../../fixtures/design/status.json"),
    "/api/healthspan": () => import("../../fixtures/design/today_healthspan.json"),
    "/api/episodes": () => import("../../fixtures/design/today_episodes.json"),
    "/api/decisions": () => import("../../fixtures/design/today_decisions.json"),
    "/api/protocol/today": () => import("../../fixtures/design/protocol_today.json"),
  },
};

/** The `?screen=` value of the current page, lower-cased; empty outside a screenshot URL. */
function screenParam(): string {
  if (typeof window === "undefined") return "";
  return (new URLSearchParams(window.location.search).get("screen") ?? "").toLowerCase();
}

/** The set the current page reads: `design` for a screenshot URL (`?screen=`), else `capture`. */
export function fixtureSet(): FixtureSet {
  if (typeof window === "undefined") return "capture";
  return new URLSearchParams(window.location.search).has("screen") ? "design" : "capture";
}

/** True on a `?screen=…-error` URL: every request fails as if the backend were off the network. */
export function fixtureUnreachable(): boolean {
  return screenParam().endsWith("-error");
}

/** The `-empty` state's answer for `route`, or `undefined` to serve the set's file unchanged. */
function emptyAnswer(route: string, data: unknown): unknown {
  if (route === "/api/episodes" || route === "/api/decisions") return [];
  if (route === "/api/session/current") return null;
  if (route === "/api/status") {
    const status = data as Status;
    return {
      ...status,
      source: "glasses",
      session: null,
      health: { ...status.health, phone: null, ok: false, problems: ["phone_disconnected"] },
    } satisfies Status;
  }
  return data;
}

/** The captured payload for `path` (query string ignored), or `undefined` when none was captured. */
export async function loadFixture(path: string): Promise<unknown> {
  const route = path.split("?")[0];
  if (route === "/api/protocol/today") return structuredClone(await protocolState());
  const set = SETS[fixtureSet()];
  // `/api/session/current` is the `session` object inside the same set's `/api/status`.
  const load = route === "/api/session/current" ? set["/api/status"] : set[route];
  if (!load) return undefined;
  let data = (await load()).default;
  if (route === "/api/session/current") data = (data as Status).session ?? null;
  return screenParam().endsWith("-empty") ? emptyAnswer(route, data) : data;
}

// ---------------------------------------------------------------------------
// Protocol writes, in memory for the page's life
// ---------------------------------------------------------------------------

const EVERY_DAY = [0, 1, 2, 3, 4, 5, 6];
const protocols = new Map<string, Promise<ProtocolToday>>();

/** Today's protocol as this page has changed it: one copy per fixture set, `-empty` starting with no items. */
function protocolState(): Promise<ProtocolToday> {
  const set = fixtureSet();
  const empty = screenParam().endsWith("-empty");
  const key = `${set}${empty ? "-empty" : ""}`;
  let state = protocols.get(key);
  if (!state) {
    state = SETS[set]["/api/protocol/today"]().then(({ default: data }) => {
      const today = structuredClone(data as ProtocolToday);
      return empty ? { ...today, items: [] } : today;
    });
    protocols.set(key, state);
  }
  return state;
}

/** The weekday of an ISO date, 0 = Monday, as the backend counts `days`. */
function weekday(day: string): number {
  return (new Date(`${day}T12:00:00`).getDay() + 6) % 7;
}

export type FixtureWrite = { ok: true; data: unknown } | { ok: false; status: number };

/**
 * What the backend answers to a protocol write (docs/API.md "The protocol"),
 * applied to the in-memory copy. Any other write answers 405: the rest of the
 * fixtures are read-only captures.
 */
export async function writeFixture(path: string, method: string, body: unknown): Promise<FixtureWrite> {
  const route = path.split("?")[0];
  const today = await protocolState();

  if (method === "POST" && route === "/api/protocol") {
    const fields = body as NewProtocolItem;
    const item: ProtocolItem = {
      id: `pi_${Math.random().toString(16).slice(2, 10)}`,
      name: fields.name,
      kind: fields.kind,
      window_start: fields.window_start,
      window_end: fields.window_end,
      days: fields.days ?? EVERY_DAY,
      created_t: Date.now() / 1000,
    };
    if (item.days.includes(weekday(today.day))) {
      today.items.push({ ...item, status: "waiting", seen_t: null, evidence_ref: null, updated_t: null });
      today.items.sort((a, b) => a.window_start.localeCompare(b.window_start));
    }
    return { ok: true, data: item };
  }

  const match = /^\/api\/protocol\/([^/]+)(?:\/(done|undo))?$/.exec(route);
  if (!match) return { ok: false, status: 405 };
  const id = decodeURIComponent(match[1]);
  const index = today.items.findIndex((item) => item.id === id);
  if (index < 0) return { ok: false, status: 404 };

  if (method === "DELETE" && !match[2]) {
    today.items.splice(index, 1);
    return { ok: true, data: { id, removed: true } };
  }
  if (method === "POST" && match[2]) {
    const row: ProtocolTodayItem = {
      ...today.items[index],
      status: match[2] === "done" ? "done" : "undone",
      updated_t: Date.now() / 1000,
    };
    today.items[index] = row;
    return { ok: true, data: { ...row, day: today.day } };
  }
  return { ok: false, status: 405 };
}
