/**
 * Small formatters shared by the Brian panels. Locale is pinned to en-US so
 * the server and the first client render produce identical strings.
 */

/** Signed number with a true minus sign: `+12%`, `−0.4`, `0 min`. */
export const fmtSigned = (n: number, digits = 0, unit = ""): string =>
  `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(digits)}${unit}`;

/** Grouped number with at most one decimal: `8,533.3`, `35`. */
export const fmtNum = (n: number): string => n.toLocaleString("en-US", { maximumFractionDigits: 1 });

/** Renders a missing measurement as a dash instead of inventing a value. */
export const orDash = (n: number | null, f: (n: number) => string): string => (n === null ? "—" : f(n));
