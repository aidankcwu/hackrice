/**
 * Fixtures mode (`NEXT_PUBLIC_FIXTURES=1`): the web twin of the native app's
 * `-demo`. Two sets of the same files, keyed here by the route each one answers:
 *
 *   phone/fixtures/*.json         copies of `ios/Brian/Fixtures/`, captured from the
 *                                 backend in `--source sim` (docs/STATE.md §4). Never edited.
 *   phone/fixtures/design/*.json  the captures with the values the human set for the
 *                                 design screenshots: hours_today 1.4, overall 71, the
 *                                 session started 42 min ago, and every measured
 *                                 provenance source "seeded", so the chip reads "Seeded".
 *
 * A screenshot URL (one that carries `?screen=`) reads the design set; any other
 * URL reads the captures. Loaded on first use, so a live build never downloads them.
 *
 * Two screenshot states change what the design set answers, by the `?screen=` suffix:
 *
 *   `-empty`  nothing seen yet today: no session, no episodes, no decisions, and the
 *             glasses app not connected. The hero keeps the design payload.
 *   `-error`  the backend never answers (`fixtureUnreachable()`).
 */
import type { Status } from "./types";

type Loader = () => Promise<{ default: unknown }>;
export type FixtureSet = "capture" | "design";

const SETS: Record<FixtureSet, Record<string, Loader>> = {
  capture: {
    "/api/status": () => import("../../fixtures/status.json"),
    "/api/healthspan": () => import("../../fixtures/today_healthspan.json"),
    "/api/episodes": () => import("../../fixtures/today_episodes.json"),
    "/api/decisions": () => import("../../fixtures/today_decisions.json"),
  },
  design: {
    "/api/status": () => import("../../fixtures/design/status.json"),
    "/api/healthspan": () => import("../../fixtures/design/today_healthspan.json"),
    "/api/episodes": () => import("../../fixtures/design/today_episodes.json"),
    "/api/decisions": () => import("../../fixtures/design/today_decisions.json"),
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
  const set = SETS[fixtureSet()];
  // `/api/session/current` is the `session` object inside the same set's `/api/status`.
  const load = route === "/api/session/current" ? set["/api/status"] : set[route];
  if (!load) return undefined;
  let data = (await load()).default;
  if (route === "/api/session/current") data = (data as Status).session ?? null;
  return screenParam().endsWith("-empty") ? emptyAnswer(route, data) : data;
}
