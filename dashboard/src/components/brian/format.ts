/**
 * Small formatters shared by the Bryan panels. Locale is pinned to en-US so
 * the server and the first client render produce identical strings.
 *
 * Everything here goes through `Intl.*` or `toFixed`; no date or number format
 * is spelled out by hand (checklist: "Dates/numbers via Intl.*").
 */

/** Signed number with a true minus sign: `+12%`, `−0.4`, `0 min`. */
export const fmtSigned = (n: number, digits = 0, unit = ""): string =>
  `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(digits)}${unit}`;

/** Grouped number with at most one decimal: `8,533.3`, `35`. */
export const fmtNum = (n: number): string => n.toLocaleString("en-US", { maximumFractionDigits: 1 });

/** Grouped integer: `9,100`. */
export const fmtInt = (n: number): string => Math.round(n).toLocaleString("en-US");

/** Renders a missing measurement as a dash instead of inventing a value. */
export const orDash = (n: number | null, f: (n: number) => string): string => (n === null ? "—" : f(n));

/**
 * Decimal hours after local midnight as `HH:MM` — `23.67` → `23:40`. Hours past
 * 24 wrap, so a 25.5 bedtime reads `01:30`. Formatted through `Intl` on a fixed
 * UTC instant so the string never shifts with the reader's zone.
 */
const HHMM = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
});

export const fmtHhmm = (hh: number): string => {
  const minutes = Math.round(((hh % 24) + 24) % 24 * 60);
  return HHMM.format(new Date(Date.UTC(2000, 0, 1, 0, minutes)));
};

/** `+1.8` / `−0.6` / `0.0` — the hero number without its unit. */
export const fmtHoursValue = (h: number): string =>
  `${h > 0 ? "+" : h < 0 ? "−" : ""}${Math.abs(h).toFixed(1)}`;
