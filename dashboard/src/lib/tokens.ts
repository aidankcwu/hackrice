/**
 * Project Brian design tokens — the JavaScript copy.
 *
 * Source of truth for anything that needs a literal colour at runtime (recharts
 * fills, inline SVG). The CSS copy lives in `src/app/globals.css` as custom
 * properties and Tailwind `@theme` colours; `design-system/brian/MASTER.md`
 * documents both. Keep the three in step — the values are the token table in
 * `.claude/skills/brian-ui/SKILL.md`.
 */
export const T = {
  bg: "#FFFFFF",
  surface: "#F4F4F5",
  surface2: "#EAEAEC",
  ink: "#111111",
  text: "#1F1F23",
  muted: "#6B6B73",
  line: "#E2E2E6",
  earn: "#15803D",
  earnSoft: "#E8F5EC",
  cost: "#C62828",
  costSoft: "#FBEAEA",
  /**
   * The circadian instrument and nothing else: one accent, one meaning (law 5).
   * Any other panel reaching for this colour is a bug, not a theme choice.
   */
  clock: "#1D4ED8",
  header: "#000000",
  radius: 20,
  radiusPin: 16,
} as const;

/** The type scale, in px (SKILL.md): hero · tile · section · body · secondary · caption. */
export const TYPE = {
  hero: 72,
  tile: 40,
  section: 24,
  body: 16,
  secondary: 14,
  caption: 12,
} as const;

/** Healthy-life hours as a signed string: `+1.8 h`, `−0.4 h`, `0.0 h`. */
export const fmtH = (h: number): string =>
  `${h > 0 ? "+" : h < 0 ? "−" : ""}${Math.abs(h).toFixed(1)} h`;

/** Colour reserved for data: green earns, red costs, grey for a wash. */
export const tone = (h: number): string => (h > 0.05 ? T.earn : h < -0.05 ? T.cost : T.muted);
