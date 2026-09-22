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
 */
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

/** The set the current page reads: `design` for a screenshot URL (`?screen=`), else `capture`. */
export function fixtureSet(): FixtureSet {
  if (typeof window === "undefined") return "capture";
  return new URLSearchParams(window.location.search).has("screen") ? "design" : "capture";
}

/** The captured payload for `path` (query string ignored), or `undefined` when none was captured. */
export async function loadFixture(path: string): Promise<unknown> {
  const load = SETS[fixtureSet()][path.split("?")[0]];
  return load ? (await load()).default : undefined;
}
