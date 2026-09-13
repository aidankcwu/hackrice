/**
 * Small pure formatters shared by the adapter and the shaper. No I/O, no React.
 */

export const round1 = (x: number): number => Math.round(x * 10) / 10;
export const round2 = (x: number): number => Math.round(x * 100) / 100;

/** `4.2` → "4.2", `2` → "2", `44.04` → "44" — a `%g`-style number for prose. */
export const trimFixed = (x: number, dp = 1): string => Number(x.toFixed(dp)).toString();

/**
 * Seeded `bed_time` is hours after local midnight, so 00:45 arrives as 0.75.
 * Anything before noon is treated as after midnight and moved past 24 so
 * medians and shifts against a 23:00 habit are arithmetic, not wrap-around.
 */
export const normaliseBedtime = (v: number): number => (v < 12 ? v + 24 : v);

/** Decimal hours (may exceed 24) → "HH:MM" on a 24 h clock; 24.75 → "00:45". */
export function hhmm(hours: number): string {
  const totalMin = Math.round((((hours % 24) + 24) % 24) * 60);
  const hh = Math.floor(totalMin / 60) % 24;
  const mm = totalMin % 60;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

/** Local decimal hour of a unix-seconds timestamp (8:30:00 local → 8.5). */
export function localDecimalHour(unixSeconds: number): number {
  const d = new Date(unixSeconds * 1000);
  return d.getHours() + d.getMinutes() / 60 + d.getSeconds() / 3600;
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

/** "2026-09-12" → "Sat". Noon local avoids the UTC-midnight rollover a bare date would parse to. */
export function shortWeekday(isoDate: string): string {
  return WEEKDAYS[new Date(`${isoDate}T12:00:00`).getDay()] ?? "—";
}

export function median(values: number[]): number | undefined {
  if (values.length === 0) return undefined;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export function mean(values: number[]): number | undefined {
  if (values.length === 0) return undefined;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

/** A finite number from a loosely typed record, or undefined (absent, null, NaN). */
export function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/** Drop `undefined` values so the engine request carries only what was measured. */
export function compact<T extends object>(obj: T): T {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined)) as T;
}
