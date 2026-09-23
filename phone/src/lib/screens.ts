/** The app's screens: four tabs, and the rest from the menu (pushed, with a back button). */
export type ScreenId =
  | "today"
  | "calendar"
  | "analysis"
  | "protocol"
  | "settings"
  | "library"
  | "treatments"
  | "tests"
  | "biomarkers"
  | "devices"
  | "concierge"
  | "sources"
  | "claims"
  | "find";

export const SCREENS: Record<ScreenId, { title: string; href: string }> = {
  today: { title: "Today", href: "/" },
  calendar: { title: "Calendar", href: "/calendar" },
  analysis: { title: "Analysis", href: "/analysis" },
  protocol: { title: "Protocol", href: "/protocol" },
  settings: { title: "Settings", href: "/settings" },
  library: { title: "Protocol library", href: "/library" },
  treatments: { title: "Treatments", href: "/treatments" },
  tests: { title: "Tests", href: "/tests" },
  biomarkers: { title: "Biomarkers", href: "/biomarkers" },
  devices: { title: "Devices", href: "/devices" },
  concierge: { title: "Concierge", href: "/concierge" },
  sources: { title: "Sources", href: "/sources" },
  claims: { title: "What we don’t claim", href: "/claims" },
  find: { title: "Find my protocol", href: "/find" },
};

/** The tab bar, in order. Everything else is pushed from the menu, never a tab. */
export const TABS: readonly ScreenId[] = ["today", "calendar", "analysis", "protocol"];

export type SearchParams = Record<string, string | string[] | undefined>;

export interface FixtureView {
  /** The screen that renders. */
  screen: ScreenId;
  /** The full `?screen=` value, e.g. `today-empty`, for a screen that has named states. */
  state: string;
  theme?: "light" | "dark";
  /** Text size multiplier, 1–3; 2 stands in for accessibility XXXL. */
  scale?: number;
}

const first = (value: string | string[] | undefined): string =>
  (Array.isArray(value) ? value[0] : value) ?? "";

/** A `?screen=` name's prefix picks the screen; the first match wins. */
const PREFIXES: readonly (readonly [prefix: string, screen: ScreenId])[] = [
  ["settings", "settings"],
  ["calendar", "calendar"],
  ["analysis", "analysis"],
  ["protocol", "protocol"],
  ["additem", "protocol"],
  ["library", "library"],
  ["treatments", "treatments"],
  ["tests", "tests"],
  ["biomarkers", "biomarkers"],
  ["devices", "devices"],
  ["concierge", "concierge"],
  ["sources", "sources"],
  ["claims", "claims"],
  ["find", "find"],
];

/**
 * Fixtures mode only: `?screen=<name>&mode=<light|dark>&scale=<1|2>`, so every
 * screenshot is one URL against `/`. The name's prefix picks the screen
 * (`today-empty` → Today, `additem` → Protocol); an unknown name is Today.
 * A `?screen=` URL also reads the design fixtures (`src/lib/fixtures.ts`).
 */
export function readFixtureView(params: SearchParams): FixtureView {
  const state = first(params.screen).toLowerCase();
  const screen: ScreenId = PREFIXES.find(([prefix]) => state.startsWith(prefix))?.[1] ?? "today";
  const mode = first(params.mode);
  const scale = Number(first(params.scale));
  return {
    screen,
    state,
    theme: mode === "light" || mode === "dark" ? mode : undefined,
    scale: scale >= 1 && scale <= 3 ? scale : undefined,
  };
}

/**
 * Fixtures mode only: the screenshot params (`screen`, `mode`, `scale`) as a query
 * string, e.g. `?screen=today&mode=dark`, so a pushed detail keeps the same
 * fixtures, appearance and text size. Empty when there are none.
 */
export function fixtureQuery(params: SearchParams): string {
  const query = new URLSearchParams();
  for (const key of ["screen", "mode", "scale"]) {
    const value = first(params[key]);
    if (value) query.set(key, value);
  }
  const text = query.toString();
  return text ? `?${text}` : "";
}
