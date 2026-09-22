/** The app's three screens (IOS_SPEC "Structure"): two tabs, and Settings from the gear. */
export type ScreenId = "today" | "protocol" | "settings";

export const SCREENS: Record<ScreenId, { title: string; href: string }> = {
  today: { title: "Today", href: "/" },
  protocol: { title: "Protocol", href: "/protocol" },
  settings: { title: "Settings", href: "/settings" },
};

/** The tab bar, in order. Settings is pushed from the gear, never a tab. */
export const TABS: readonly ScreenId[] = ["today", "protocol"];

export type SearchParams = Record<string, string | string[] | undefined>;

export interface FixtureView {
  /** The tab that renders. */
  screen: ScreenId;
  /** The full `?screen=` value, e.g. `today-empty`, for a screen that has named states. */
  state: string;
  theme?: "light" | "dark";
  /** Text size multiplier, 1–3; 2 stands in for accessibility XXXL. */
  scale?: number;
}

const first = (value: string | string[] | undefined): string =>
  (Array.isArray(value) ? value[0] : value) ?? "";

/**
 * Fixtures mode only: `?screen=<name>&mode=<light|dark>&scale=<1|2>`, so every
 * screenshot is one URL against `/`. The name's prefix picks the screen
 * (`today-empty` → Today, `additem` → Protocol); an unknown name is Today.
 * A `?screen=` URL also reads the design fixtures (`src/lib/fixtures.ts`).
 */
export function readFixtureView(params: SearchParams): FixtureView {
  const state = first(params.screen).toLowerCase();
  const screen: ScreenId = state.startsWith("settings")
    ? "settings"
    : state.startsWith("protocol") || state.startsWith("additem")
      ? "protocol"
      : "today";
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
