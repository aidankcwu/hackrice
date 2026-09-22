/**
 * The five instrument tiles — "What only your glasses can see" (screens.md §1.2).
 *
 * Pure derivations: one `InstrumentTile` per layer, built only from fields the
 * healthspan payload actually carries (`observations`, `provenance`, `forecast`,
 * `layers`, plus the trailing-days `observations` the week route returns). When
 * a tile's number has no real source the tile still renders — `measured: false`,
 * the voice.md unmeasured string, and the payload's own reason — because a
 * fabricated number is worse than an admitted gap (R1).
 *
 * Nothing here formats for a particular screen size and nothing imports React:
 * `Instruments.tsx` draws what this returns and `instruments.test.ts` asserts it.
 */

import {
  FACTOR_METRIC,
  FACTOR_ORIGIN,
  factorProvenance,
  glassesGap,
  LIVE_SOURCES,
  PROVENANCE_LABEL,
  wearableFor,
  type PageSource,
  type Provenance,
} from "./provenance";

// ---------------------------------------------------------------------------
// What the payload gives us
// ---------------------------------------------------------------------------

/** `provenance[key]` in the healthspan payload (backend/pipeline/scoring/healthspan.py). */
export interface ObsProvenance {
  /** `live` = summed over the glasses' episodes, `seeded` = an integration row as-is, `derived` = a proxy of either, `missing` = no measurement at all. */
  source: "live" | "seeded" | "derived" | "missing";
  /** Which stream filed it: `glasses`, `phone`, `whoop`, `apple_watch`, `pvt`, `openaq`, `user`. */
  basis: string;
  /** One sentence on how the number was arrived at, or why there isn't one. */
  detail: string;
}

/**
 * Everything the five tiles read. Deliberately narrower than the whole
 * healthspan payload so a tile cannot start depending on a field by accident.
 *
 * `trailing` is the oldest-first trailing-days window `GET /api/healthspan?days=7`
 * returns (`days[].observations`); today is its last entry. An empty array
 * simply means no sparkline — never a flat line at zero.
 */
export interface InstrumentSource {
  observations: Readonly<Record<string, number>>;
  provenance: Readonly<Record<string, ObsProvenance>>;
  forecast: {
    melatonin_delay_min: number;
    drivers: readonly string[];
  };
  /** Habitual bedtime, decimal hours after local midnight (23.5 = 23:30). */
  bedtime_hh: number;
  /** Trailing days, oldest first, each the lite payload's `observations`. */
  trailing: ReadonlyArray<Readonly<Record<string, number>>>;
}

// ---------------------------------------------------------------------------
// Strings (voice.md) and the tokens only these tiles use
// ---------------------------------------------------------------------------

/** voice.md, verbatim. Shown in place of a number whenever nothing measured it. */
export const UNMEASURED = "Unmeasured today — scored at the population average, earns nothing.";

/** voice.md, verbatim — Mind's own unmeasured line, because a PVT is something the wearer can go and do. */
export const PVT_STALE = "Take the 3-minute test to update Mind.";

/** The one accent the tokens reserve for the circadian instrument (SKILL.md `--clock`). */
export const CLOCK_ACCENT = "#1D4ED8";

// ---------------------------------------------------------------------------
// Status dot
// ---------------------------------------------------------------------------

/**
 * screens.md §1.2: "green when at/above target, amber within 30 %, red below;
 * grey when unmeasured". `at` is the dose, `target` the layer target it is
 * judged against, and `lowerIsBetter` flips both comparisons (air, noise,
 * reaction time — where the target is a ceiling).
 */
export type Status = "good" | "near" | "poor" | "unmeasured";

export function statusOf(at: number | null, target: number, lowerIsBetter = false): Status {
  if (at === null || !Number.isFinite(at) || target <= 0) return "unmeasured";
  const ratio = lowerIsBetter ? target / at : at / target;
  if (ratio >= 1) return "good";
  return ratio >= 0.7 ? "near" : "poor";
}

// ---------------------------------------------------------------------------
// Provenance chip
// ---------------------------------------------------------------------------

/** A `basis` that names a wearable device; `phone`, `openaq` and the rest do not. */
const DEVICE_BASIS = /whoop|fitbit|healthkit|apple_watch/;

/**
 * The payload's provenance → one chip, with the same labels `provenance.ts`
 * gives the By-layer panel. A wearable row earns its device's chip (Fitbit,
 * Apple Health, WHOOP) only when a connected device wrote it: `live`, or a
 * `derived` conversion of a live-device row. The demo seed is "Seeded" whatever
 * device it imitates, and `phone` / `openaq` rows read "Seeded" rather than
 * claiming a live sensor. A missing observation is "Imputed" whatever filed the
 * attempt, matching how the engine scores it at the population reference.
 */
export function chipFor(p: ObsProvenance | undefined): Provenance {
  if (!p || p.source === "missing") return "imputed";
  const basis = p.basis.toLowerCase();
  if (basis === "glasses") return "glasses";
  if (basis === "pvt" || basis === "user") return "entered";
  const live = p.source === "live" || (p.source === "derived" && LIVE_SOURCES.has(basis));
  return live && DEVICE_BASIS.test(basis) ? wearableFor({ source: "live", basis }) : "seeded";
}

export const chipLabel = (p: Provenance): string => PROVENANCE_LABEL[p];

/** The engine factor fields a tile's provenance is built from. */
export interface FactorRow {
  key: string;
  label: string;
  measured: boolean;
}

/**
 * Leading indicators a tile reads that are not engine factors, so no
 * `factors[].measured` exists for them. Only `night_screen_min` is read: the
 * clock tile's forecast delay is computed from it alone (`forecast_tonight`).
 * It is the glasses' `screen_block` minutes after 22:00, summed by the engine's
 * `observations_from_app`, so its presence in `observations` on a day the
 * glasses filed any episode is the measurement (the engine writes 0 even on a
 * day they did not — `glassesGap` rules that out) and `FACTOR_ORIGIN` names
 * the stream.
 */
const LEADING_READ: Readonly<Record<string, (v: number) => string>> = {
  night_screen_min: (v) => `${withUnit(v, "min")} of screens after 22:00`,
};

/**
 * `InstrumentSource.provenance` for a payload that carries no per-key
 * provenance of its own: each factor's chip comes from `factorProvenance` (the
 * By-layer panel's derivation), written back as the `ObsProvenance` that
 * `chipFor` reads as that same chip. Never a hard-coded source: a factor the
 * glasses measured is Glasses, a HealthKit row is Apple Health, a demo-seed row
 * is Seeded, and an unmeasured one is `missing` with the voice.md string — or,
 * when it is a glasses value the day's coverage cannot back, with that reason
 * ("no glasses episodes today").
 *
 * With `observations`, the leading indicators in `LEADING_READ` get the same
 * derivation, measured exactly when the engine put a number in `observations`
 * (and the glasses were on). A factor of the same key, should the engine grow
 * one, wins.
 */
export function instrumentProvenance(
  factors: readonly FactorRow[],
  source: PageSource,
  observations?: Readonly<Record<string, number>>,
): Record<string, ObsProvenance> {
  const leading: FactorRow[] =
    observations === undefined
      ? []
      : Object.entries(LEADING_READ)
          .filter(([key]) => !factors.some((f) => f.key === key))
          .map(([key, describe]) => {
            const v = finite(observations[key]);
            return { key, label: v === null ? UNMEASURED : describe(v), measured: v !== null };
          });
  return Object.fromEntries(
    [...leading, ...factors].map((f): [string, ObsProvenance] => {
      const chip = factorProvenance(f.key, f.measured, source);
      switch (chip) {
        case "imputed":
          return [f.key, { source: "missing", basis: FACTOR_ORIGIN[f.key] ?? "glasses", detail: glassesGap(f.key, source) ?? UNMEASURED }];
        case "seeded":
          // The row's own source, as the backend's `seeded` basis carries it.
          return [f.key, { source: "seeded", basis: source.wearable_sources?.[FACTOR_METRIC[f.key] ?? f.key] ?? "seed", detail: f.label }];
        case "entered":
          return [f.key, { source: "live", basis: "user", detail: f.label }];
        default:
          // glasses, whoop, fitbit, healthkit: the stream is the basis.
          return [f.key, { source: "live", basis: chip, detail: f.label }];
      }
    }),
  );
}

/**
 * The payload's `observations` less every glasses value the day's coverage
 * cannot back, so a tile that reads a number straight from `observations`
 * finds none rather than the engine's default 0.
 */
export function coveredObservations(
  observations: Readonly<Record<string, number>>,
  source: PageSource,
): Record<string, number> {
  return Object.fromEntries(Object.entries(observations).filter(([key]) => glassesGap(key, source) === null));
}

/** Everything the five tiles read, from the shaped dashboard's own fields. */
export function instrumentSource(d: {
  factors: readonly FactorRow[];
  source: PageSource;
  observations: Readonly<Record<string, number>>;
  forecast: InstrumentSource["forecast"];
  bedtime_hh: number;
}): InstrumentSource {
  return {
    observations: coveredObservations(d.observations, d.source),
    provenance: instrumentProvenance(d.factors, d.source, d.observations),
    forecast: d.forecast,
    bedtime_hh: d.bedtime_hh,
    // Empty until the days=7 window is wired: no sparkline, never a flat line at zero.
    trailing: [],
  };
}

// ---------------------------------------------------------------------------
// Formatting helpers (en-US, pinned so server and client render the same string)
// ---------------------------------------------------------------------------

const NBSP_THIN = " ";

/** `41 min`, `6.3 h`, `31 µg/m³` — a thin space before the unit, as voice.md asks. */
export const withUnit = (n: number, unit: string, digits = 0): string =>
  `${n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}${NBSP_THIN}${unit}`;

/** Signed with a true minus sign: `+38 ms`, `−12 %`, `0 min`. */
export const signedUnit = (n: number, unit: string, digits = 0): string =>
  `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(digits)}${NBSP_THIN}${unit}`;

/** Decimal hours → `23:40`. Wraps a day, so 24.5 reads 00:30. */
export function clockTime(hh: number): string {
  const total = Math.round(((hh % 24) + 24) % 24 * 60);
  const h = Math.floor(total / 60) % 24;
  const m = total % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

const finite = (v: number | undefined): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

// ---------------------------------------------------------------------------
// The tile
// ---------------------------------------------------------------------------

/** Which lucide icon each layer gets, fixed by SKILL.md; `Instruments.tsx` maps it. */
export type InstrumentIcon = "clock" | "sun" | "users" | "trees" | "brain";

export interface DetailRow {
  label: string;
  /** Already formatted, or the dash when that part is itself unmeasured. */
  value: string;
}

export interface InstrumentTile {
  key: "clock" | "light" | "people" | "outside" | "mind";
  /** The heading on the tile, and the sheet's title. */
  title: string;
  icon: InstrumentIcon;
  /** Hex accent, only on the clock tile; undefined everywhere else (one accent, one meaning). */
  accent?: string;
  /** True when something real measured the tile's number today. */
  measured: boolean;
  /** The 40 px number, or null when unmeasured — never a zero standing in for one. */
  number: string | null;
  /** The 14 px line under the number. Empty when unmeasured; `reason` carries the line instead. */
  reading: string;
  /** Why there is no number, from the payload's own provenance detail. */
  reason: string;
  /** The voice.md line shown in place of the number. */
  unmeasuredNote: string;
  status: Status;
  chip: Provenance;
  /** Hover/title text for the chip. */
  chipDetail: string;
  /** 7 points oldest-first for the sparkline, shorter or empty when days are uncovered. */
  series: number[];
  /** What the sparkline plots, named so the SVG has an accessible description. */
  seriesLabel: string;
  /** Rows of the detail sheet, in the order screens.md lists them. */
  detail: DetailRow[];
  /** The published basis, muted, ≤ 5 words as voice.md asks. */
  evidence: string;
  /** A sheet action, when the wearer can go and measure the thing themselves. */
  action?: { label: string; href: string };
}

/** Series for one observation key across the trailing window; days without it are dropped. */
function seriesOf(src: InstrumentSource, key: string): number[] {
  return src.trailing.map((o) => finite(o[key])).filter((v): v is number => v !== null);
}

const dash = "—";

/** A value row for the detail sheet that never invents a number. */
const row = (label: string, value: number | null, fmt: (n: number) => string): DetailRow => ({
  label,
  value: value === null ? dash : fmt(value),
});

// Targets the dots are judged against. Each is the published threshold the
// factor's own source names, the same ones `brian_score.FACTORS` curves against.
const TARGET = {
  /** Brown 2022: ≥ 250 lx melanopic by day; the engine's dose is minutes above it. */
  day_light_min: 45,
  /** Holt-Lunstad 2010 social integration index, the engine's own 0–100 scale. */
  social_index: 70,
  /** White 2019: ≥ 120 min/week in nature. */
  nature_min_wk: 120,
  /** WHO 2021 annual guideline, 5 µg/m³. */
  pm25: 5,
  /** A reaction time at or under the wearer's own baseline, in SD units. */
  rt_z: 0,
} as const;

function clockTile(src: InstrumentSource): InstrumentTile {
  const screens = src.provenance.night_screen_min;
  const delay = src.forecast.melatonin_delay_min;
  // The engine forecasts a *delay*, not an absolute onset, and it reads that
  // delay off tonight's screen minutes — so the tile is measured exactly when
  // those minutes are.
  const measured = screens !== undefined && screens.source !== "missing";
  const drift = seriesOf(src, "night_screen_min").map((m) => (m / 60) * 15);
  return {
    key: "clock",
    title: "Your clock",
    icon: "clock",
    accent: CLOCK_ACCENT,
    measured,
    number: measured ? signedUnit(delay, "min") : null,
    reading: measured
      ? `${delay > 0 ? `${withUnit(delay, "min")} later than your habit` : "on your habitual phase"} · bright light before 09:00 tomorrow moves it earlier`
      : "",
    reason: screens?.detail ?? "no screen minutes measured tonight",
    unmeasuredNote: UNMEASURED,
    status: measured ? (delay <= 0 ? "good" : delay <= 30 ? "near" : "poor") : "unmeasured",
    chip: chipFor(screens),
    chipDetail: screens?.detail ?? "",
    series: drift,
    seriesLabel: "clock delay per day, last 7 days",
    detail: [
      row("Predicted delay tonight", measured ? delay : null, (n) => signedUnit(n, "min")),
      row("Habitual bedtime", src.bedtime_hh, clockTime),
      row("Screens after 22:00", finite(src.observations.night_screen_min), (n) => withUnit(n, "min")),
      { label: "How it is predicted", value: "15 min of delay per hour of evening screen light" },
    ],
    evidence: "Brown 2022",
  };
}

function lightTile(src: InstrumentSource): InstrumentTile {
  const p = src.provenance.day_light_min;
  const bright = finite(src.observations.day_light_min);
  const nightLux = finite(src.observations.night_light_lux);
  const screens = finite(src.observations.night_screen_min);
  const reading = [
    nightLux === null ? null : `${withUnit(nightLux, "lx")} during sleep`,
    screens === null ? null : `${withUnit(screens, "min")} of screens after 22:00`,
  ].filter((s): s is string => s !== null);
  return {
    key: "light",
    title: "Light",
    icon: "sun",
    measured: bright !== null,
    number: bright === null ? null : withUnit(bright, "min"),
    reading: reading.join(" · "),
    reason: p?.detail ?? "no light measurement today",
    unmeasuredNote: UNMEASURED,
    status: statusOf(bright, TARGET.day_light_min),
    chip: chipFor(p),
    chipDetail: p?.detail ?? "",
    series: seriesOf(src, "day_light_min"),
    seriesLabel: "bright minutes per day, last 7 days",
    detail: [
      row("Bright minutes today", bright, (n) => withUnit(n, "min")),
      { label: "Daytime target", value: `${withUnit(TARGET.day_light_min, "min")} above 250 lx melanopic` },
      row("Light during sleep", nightLux, (n) => withUnit(n, "lx")),
      { label: "Sleep target", value: `under ${withUnit(1, "lx")} melanopic` },
      row("Screens after 22:00", screens, (n) => withUnit(n, "min")),
    ],
    evidence: "Brown 2022",
  };
}

function peopleTile(src: InstrumentSource): InstrumentTile {
  const p = src.provenance.social_index;
  const index = finite(src.observations.social_index);
  return {
    key: "people",
    title: "People",
    icon: "users",
    measured: index !== null,
    // The glasses measure conversation minutes and distinct encounters; the
    // engine folds both into one 0–100 integration index, which is what the
    // number can honestly be. The minutes behind it are in `chipDetail`.
    number: index === null ? null : Math.round(index).toLocaleString("en-US"),
    reading: index === null ? "" : "social integration, 0 to 100 · never names, never records words",
    reason: p?.detail ?? "no conversation measured today",
    unmeasuredNote: UNMEASURED,
    status: statusOf(index, TARGET.social_index),
    chip: chipFor(p),
    chipDetail: p?.detail ?? "",
    series: seriesOf(src, "social_index"),
    seriesLabel: "social integration per day, last 7 days",
    detail: [
      row("Integration index", index, (n) => Math.round(n).toLocaleString("en-US")),
      { label: "Target", value: `${TARGET.social_index} of 100` },
      { label: "What the glasses counted", value: p?.detail ?? dash },
      { label: "What they never keep", value: "no names, no identities, no words" },
    ],
    evidence: "Holt-Lunstad 2010",
  };
}

function outsideTile(src: InstrumentSource): InstrumentTile {
  const p = src.provenance.nature_min_wk;
  const nature = finite(src.observations.nature_min_wk);
  const air = src.provenance.pm25;
  const pm25 = finite(src.observations.pm25);
  const noise = finite(src.observations.noise_night_db);
  const reading = [
    pm25 === null ? `air ${dash}` : `air ${withUnit(pm25, "µg/m³")}`,
    noise === null ? null : `${withUnit(noise, "dB")} at night`,
  ].filter((s): s is string => s !== null);
  return {
    key: "outside",
    title: "Outside",
    icon: "trees",
    measured: nature !== null,
    number: nature === null ? null : `${Math.round(nature)} / ${TARGET.nature_min_wk}${NBSP_THIN}min`,
    reading: nature === null ? "" : `nature this week · ${reading.join(" · ")}`,
    reason: p?.detail ?? "no outdoor minutes measured this week",
    unmeasuredNote: UNMEASURED,
    status: statusOf(nature, TARGET.nature_min_wk),
    chip: chipFor(p),
    chipDetail: p?.detail ?? "",
    series: seriesOf(src, "nature_min_wk"),
    seriesLabel: "nature minutes this week, last 7 days",
    detail: [
      row("Nature minutes this week", nature, (n) => `${Math.round(n)} of ${TARGET.nature_min_wk}`),
      row("Air today", pm25, (n) => withUnit(n, "µg/m³")),
      { label: "Air guideline", value: withUnit(TARGET.pm25, "µg/m³") },
      { label: air?.source === "missing" ? "Why there is no air reading" : "Air source", value: air?.detail ?? dash },
      row("Night noise", noise, (n) => withUnit(n, "dB")),
      { label: "Night guideline", value: `under ${withUnit(45, "dB")}` },
    ],
    evidence: "White 2019 · WHO 2021",
  };
}

function mindTile(src: InstrumentSource): InstrumentTile {
  const p = src.provenance.rt_z;
  const z = finite(src.observations.rt_z);
  return {
    key: "mind",
    title: "Mind",
    icon: "brain",
    measured: z !== null,
    // The PVT files a z-score against the wearer's own baseline. Milliseconds
    // would need that baseline's SD in the payload; it is not there, so the
    // tile states the score it actually has.
    number: z === null ? null : signedUnit(z, "SD", 1),
    reading: z === null ? "" : `reaction time vs your baseline · ${z <= 0 ? "at or faster than" : "slower than"} your own days`,
    reason: p?.detail ?? "no PVT today",
    // Mind is the one tile whose gap the wearer can close in three minutes, so
    // it says so rather than only reporting that nothing measured it.
    unmeasuredNote: PVT_STALE,
    status: statusOf(z, TARGET.rt_z, true),
    chip: chipFor(p),
    chipDetail: p?.detail ?? "",
    series: seriesOf(src, "rt_z"),
    seriesLabel: "reaction time vs baseline, last 7 days",
    detail: [
      row("Reaction time vs baseline", z, (n) => signedUnit(n, "SD", 1)),
      { label: "What it is", value: "a 3-minute tap test against your own baseline" },
      { label: "Why it is not a lever", value: "a state marker: it reports the day, it is not something to set" },
      { label: p?.source === "missing" ? "Why there is no score" : "Last test", value: p?.detail ?? dash },
    ],
    evidence: "Basner 2016",
    action: { label: "Take Test", href: "/pvt" },
  };
}

/**
 * The five tiles, in the order screens.md lists them — Your clock, Light,
 * People, Outside, Mind. Always five: an unmeasured tile renders with its
 * reason, never disappears.
 */
export function instrumentTiles(src: InstrumentSource): InstrumentTile[] {
  return [clockTile(src), lightTile(src), peopleTile(src), outsideTile(src), mindTile(src)];
}

// ---------------------------------------------------------------------------
// Tonight: which of the engine's drivers is still recoverable
// ---------------------------------------------------------------------------

/**
 * The engine's forecast drivers split cleanly into things that already happened
 * and things that have not yet: the coffee and the drinks are drunk, tonight's
 * screens and tonight's bedtime are still the wearer's to choose. Each
 * recoverable one carries how much of tonight it is still holding, taken from
 * the same `LEADING` coefficients `forecast_tonight` applies:
 *
 *   night_screen_min  −0.25 h of sleep and +15 min of clock per hour
 *   late_bed_shift_min  −1.4 SRI points per 30 min
 *
 * so the sentence names the largest one by sleep cost and nothing is ranked on
 * a number the engine did not produce.
 */
export interface Recoverable {
  key: "night_screen_min" | "planned_bed_shift_min";
  /** The driver string the engine emitted, quoted back. */
  driver: string;
  /** Written in the voice.md register: the number, then the cheapest recovery. */
  sentence: string;
  /** Hours of sleep still on the table; the ranking key. */
  sleepHours: number;
}

const SLEEP_H_PER_SCREEN_HOUR = 0.25;
const CLOCK_MIN_PER_SCREEN_HOUR = 15;

export function recoverableDrivers(
  drivers: readonly string[],
  observations: Readonly<Record<string, number>>,
  bedtime_hh: number,
): Recoverable[] {
  const out: Recoverable[] = [];
  const screenDriver = drivers.find((d) => d.includes("screens after 22:00"));
  const screens = finite(observations.night_screen_min) ?? 0;
  if (screenDriver && screens > 0) {
    out.push({
      key: "night_screen_min",
      driver: screenDriver,
      sentence: `No more screens tonight gets back about ${withUnit((SLEEP_H_PER_SCREEN_HOUR * screens) / 60, "h", 1)} of sleep and ${withUnit(Math.round((CLOCK_MIN_PER_SCREEN_HOUR * screens) / 60), "min")} of clock drift.`,
      sleepHours: (SLEEP_H_PER_SCREEN_HOUR * screens) / 60,
    });
  }
  const bedDriver = drivers.find((d) => d.startsWith("bedtime"));
  // Without a measured habit there is no hour to name, and naming the engine's
  // 23:00 default as the wearer's bedtime would be a fabrication (R1).
  if (bedDriver && Number.isFinite(bedtime_hh)) {
    out.push({
      key: "planned_bed_shift_min",
      driver: bedDriver,
      sentence: `Lights out at ${clockTime(bedtime_hh)} keeps your regularity score.`,
      // A bed shift costs regularity points, not sleep hours; it ranks below a
      // screen block that is actually taking hours off tonight.
      sleepHours: 0,
    });
  }
  return out.sort((a, b) => b.sleepHours - a.sleepHours);
}

// ---------------------------------------------------------------------------
// Sparkline geometry (shared with Sparkline.tsx so it can be tested)
// ---------------------------------------------------------------------------

export interface SparkGeometry {
  width: number;
  height: number;
  /** SVG path for the line, or null when there is nothing to draw. */
  d: string | null;
  /** Centre of the last point, for the end dot. */
  last: { x: number; y: number } | null;
}

/**
 * 96 × 24, ink line, no axes (screens.md §1.2). A flat series draws down the
 * middle rather than dividing by a zero range; one point draws a dot only.
 */
export function sparkPath(values: readonly number[], width = 96, height = 24, inset = 2): SparkGeometry {
  const pts = values.filter((v) => Number.isFinite(v));
  if (pts.length === 0) return { width, height, d: null, last: null };
  const lo = Math.min(...pts);
  const hi = Math.max(...pts);
  const span = hi - lo;
  const top = inset;
  const bottom = height - inset;
  const x = (i: number): number =>
    pts.length === 1 ? width / 2 : inset + (i * (width - inset * 2)) / (pts.length - 1);
  const y = (v: number): number => (span === 0 ? height / 2 : bottom - ((v - lo) / span) * (bottom - top));
  const coords = pts.map((v, i) => ({ x: x(i), y: y(v) }));
  const last = coords[coords.length - 1];
  const d =
    coords.length === 1
      ? null
      : coords.map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(1)} ${c.y.toFixed(1)}`).join(" ");
  return { width, height, d, last };
}
