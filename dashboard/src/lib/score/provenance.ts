/**
 * Provenance: where each number on the dashboard came from. One chip per
 * metric — Glasses / WHOOP / Entered / Seeded / Imputed — and seeded data is
 * never relabelled as live.
 *
 * The origin map mirrors `observations_from_app` in the engine and the
 * adapter: which raw stream each observation is built from when it is
 * measured. Whether a wearable value is real WHOOP or the demo seed is a
 * property of the day's data, not of the key, so the caller passes it in.
 */

export type Provenance = "glasses" | "whoop" | "fitbit" | "healthkit" | "entered" | "seeded" | "imputed";

export const PROVENANCE_LABEL: Readonly<Record<Provenance, string>> = {
  glasses: "Glasses",
  whoop: "WHOOP",
  fitbit: "Fitbit",
  healthkit: "Apple Health",
  entered: "Entered",
  seeded: "Seeded",
  imputed: "Imputed",
};

/** One-line meaning for a tooltip / title. */
export const PROVENANCE_HINT: Readonly<Record<Provenance, string>> = {
  glasses: "Measured by the Ray-Ban Meta glasses (episodes from the camera pipeline)",
  whoop: "From the wearer's WHOOP data",
  fitbit: "From the wearer's Fitbit, via the Google Health API",
  healthkit: "From the wearer's Apple Health, synced by the Bryan phone app",
  entered: "Entered by the wearer (PVT, self-check)",
  seeded: "Demo seed data, not the wearer's own",
  imputed: "Not measured today: imputed at the population reference and earns nothing",
};

/** Raw stream behind each engine observation key. */
export type Origin = "glasses" | "wearable" | "entered";

export const FACTOR_ORIGIN: Readonly<Record<string, Origin>> = {
  steps: "wearable",
  vilpa_min: "wearable",
  resistance_min_wk: "glasses", // gym_session episodes booked as strength minutes
  fitness_pct: "wearable",
  gait_speed: "glasses",
  sleep_hours: "wearable",
  sri: "wearable",
  day_light_min: "glasses",
  night_light_lux: "wearable",
  social_index: "glasses",
  purpose: "entered",
  nature_min_wk: "glasses",
  noise_night_db: "wearable",
  med_adherence: "glasses",
  alcohol_drinks: "glasses",
  smoker: "entered",
  sauna_wk: "glasses",
  recovery_ratio: "wearable",
  rt_z: "entered",
  // leading indicators
  last_caffeine_hh: "glasses",
  night_screen_min: "glasses",
  planned_bed_shift_min: "wearable",
  // experience components
  recovery_score: "wearable",
  check: "entered",
};

/** Engine key -> the seeded-table metric it is read from (mirrors `_SEEDED_AS_IS` in backend healthspan.py). */
export const FACTOR_METRIC: Readonly<Record<string, string>> = {
  steps: "steps",
  vilpa_min: "vilpa_minutes",
  gait_speed: "gait_speed_ms",
  sleep_hours: "sleep_hours",
  sri: "sleep_regularity_sri",
  noise_night_db: "night_noise_db",
  smoker: "journal_nicotine",
  recovery_ratio: "hrv_rmssd_ratio",
  fitness_pct: "vo2max",
  active_energy: "active_energy",
  resting_hr: "resting_hr",
  hrv: "hrv_rmssd_ms",
  strain: "strain",
};

/**
 * The context for one key, from the day's row sources when the loader
 * supplied them: a row a connected device wrote is that device's chip, a demo
 * row is Seeded, and with no source information at all the page-wide fallback
 * applies (live backend and not demo mode -> WHOOP, as before).
 */
export function contextFor(key: string, sources: Record<string, string> | undefined, fallback: ProvenanceContext["wearable"]): ProvenanceContext {
  const metric = FACTOR_METRIC[key] ?? key;
  const src = sources?.[metric];
  if (src === undefined) return { wearable: sources === undefined ? fallback : "seeded" };
  return { wearable: wearableFor({ source: LIVE_SOURCES.has(src) ? "live" : "seeded", basis: src }, fallback) };
}

/** Seeded-table sources that are a real device (mirrors `LIVE_DAILY_SOURCES` in backend scorer.py). */
export const LIVE_SOURCES: ReadonlySet<string> = new Set(["fitbit", "apple_watch_live", "whoop_live", "healthkit"]);

export interface ProvenanceContext {
  /** Whether the day's wearable rows are the wearer's own device or the demo seed. */
  wearable: "whoop" | "fitbit" | "healthkit" | "seeded";
}

/** The engine's per-factor provenance, as the backend adapter reports it. */
export interface EngineProvenance {
  source?: string;
  basis?: string;
}

/** `provenance[key]` in the healthspan payload (backend/pipeline/scoring/healthspan.py). */
export interface ObsProvenance {
  /** `live` = summed over the glasses' episodes, `seeded` = an integration row as-is, `derived` = a proxy of either, `missing` = no measurement at all. */
  source: "live" | "seeded" | "derived" | "missing";
  /** Which stream filed it: `glasses`, `glasses + wearer report`, `phone`, `whoop`, `apple_watch`, `pvt`, `openaq`, `user`. */
  basis: string;
  /** One sentence on how the number was arrived at, or why there isn't one. */
  detail: string;
}

/** A `basis` that names a wearable device; `phone`, `openaq` and the rest do not. */
const DEVICE_BASIS = /whoop|fitbit|healthkit|apple_watch/;

/** The glasses filed it — alone, or with the wearer's own answer on top (`glasses + wearer report`). */
const isGlassesBasis = (basis: string): boolean => basis.toLowerCase().startsWith("glasses");

/**
 * The payload's provenance → one chip, with the same labels the By-layer panel
 * uses. A wearable row earns its device's chip (Fitbit, Apple Health, WHOOP)
 * only when a connected device wrote it: `live`, or a `derived` conversion of a
 * live-device row. The demo seed is "Seeded" whatever device it imitates, and
 * `phone` / `openaq` rows read "Seeded" rather than claiming a live sensor. A
 * missing observation is "Imputed" whatever filed the attempt, matching how the
 * engine scores it at the population reference.
 */
export function chipFor(p: ObsProvenance | undefined): Provenance {
  if (!p || p.source === "missing") return "imputed";
  const basis = p.basis.toLowerCase();
  if (isGlassesBasis(basis)) return "glasses";
  if (basis === "pvt" || basis === "user") return "entered";
  const live = p.source === "live" || (p.source === "derived" && LIVE_SOURCES.has(basis));
  return live && DEVICE_BASIS.test(basis) ? wearableFor({ source: "live", basis }) : "seeded";
}

/**
 * Which wearable chip a factor earns, from its own provenance row rather than
 * a page-wide flag: the backend marks a row `live` with the device as `basis`
 * when a connected device wrote it, and `seeded` for the demo seed. Demo mode
 * only changes the stage timings, not where the numbers came from.
 */
export function wearableFor(p: EngineProvenance | undefined, fallback: ProvenanceContext["wearable"] = "seeded"): ProvenanceContext["wearable"] {
  if (p?.source !== "live") return p === undefined ? fallback : "seeded";
  const basis = (p.basis ?? "").toLowerCase();
  if (basis.includes("fitbit")) return "fitbit";
  // An Apple Watch writes through HealthKit: `apple_watch_live` is Apple Health, never WHOOP.
  if (basis.includes("healthkit") || basis.includes("apple_watch")) return "healthkit";
  if (basis.includes("whoop")) return "whoop";
  return fallback === "seeded" ? "whoop" : fallback;
}

/**
 * Chip for one observation key. `measured` is the engine's `factors[].measured`
 * (or, for non-factors, whether the value was present in the request).
 */
export function provenanceOf(key: string, measured: boolean, ctx: ProvenanceContext): Provenance {
  if (!measured) return "imputed";
  const origin = FACTOR_ORIGIN[key];
  if (origin === "wearable") return ctx.wearable;
  if (origin === "entered") return "entered";
  return "glasses";
}

/**
 * Whether the glasses produced any episode today, and on any day of the scored
 * week. The backend adapter's rule (healthspan.py `_DayData.covered`): "a zero
 * from the glasses is a measurement only on a day that has episodes at all --
 * otherwise it is missing".
 */
export interface GlassesCoverage {
  today: boolean;
  week: boolean;
}

/** The slice of the payload's `DataSource` a chip depends on. */
export interface PageSource {
  mode: string;
  demo_mode?: boolean;
  wearable_sources?: Record<string, string>;
  /** Absent means unknown, and nothing is gated on it. */
  glasses_coverage?: GlassesCoverage;
  /**
   * The backend's own per-key provenance for the scored day (`/api/healthspan`).
   * Where a key has a row, it decides the chip and the gate: it is what the
   * score was actually computed from, so nothing here re-derives it.
   */
  provenance?: Readonly<Record<string, ObsProvenance>>;
}

/** The page-wide wearable fallback when the loader supplied no row sources: live backend and not demo mode -> WHOOP. */
export const wearableFallback = (source: PageSource): ProvenanceContext["wearable"] =>
  source.mode === "live" && source.demo_mode !== true ? "whoop" : "seeded";

/** Glasses keys the engine sums over the trailing week: one covered day in the window makes their zero real. */
const WEEKLY_GLASSES_KEYS: ReadonlySet<string> = new Set(["resistance_min_wk", "nature_min_wk", "sauna_wk"]);

/**
 * Why a glasses-derived key cannot be a measurement on this page, or null when
 * it can. The engine's `observations_from_app` sums episodes, so it reports 0
 * bright minutes, 0 drinks and 0 screen minutes whether or not the glasses
 * were worn; without an episode behind it that 0 is a default, not a sighting.
 *
 * With the backend's provenance for the key, its verdict is the gate: the
 * backend applies the same coverage rule (`_DayData.covered`) before scoring, so
 * a glasses key it left `missing` is the gap (with its own reason) and a key it
 * filled from another stream — the phone's bright-light row, the WHOOP drinks
 * journal — is a measurement whatever the glasses did.
 */
export function glassesGap(key: string, source: PageSource): string | null {
  const p = source.provenance?.[key];
  if (p !== undefined) return p.source === "missing" && isGlassesBasis(p.basis) ? p.detail : null;
  const coverage = source.glasses_coverage;
  if (coverage === undefined || FACTOR_ORIGIN[key] !== "glasses") return null;
  if (WEEKLY_GLASSES_KEYS.has(key)) return coverage.week ? null : "no glasses episodes this week";
  return coverage.today ? null : "no glasses episodes today";
}

/** The engine's `measured`, less any glasses value the day's coverage cannot back. */
export const measuredOnPage = (key: string, measured: boolean, source: PageSource): boolean =>
  measured && glassesGap(key, source) === null;

/**
 * One factor's chip — the same derivation the By-layer panel and the tiles both
 * use, so a tile and a layer row can never name different streams for one
 * factor. The backend's provenance row for the key when the payload carries one
 * (the stream the score was computed from); otherwise re-derived from the day's
 * row sources and glasses coverage.
 */
export function factorProvenance(key: string, measured: boolean, source: PageSource): Provenance {
  const onPage = measuredOnPage(key, measured, source);
  const p = source.provenance?.[key];
  if (p !== undefined) return onPage ? chipFor(p) : "imputed";
  return provenanceOf(key, onPage, contextFor(key, source.wearable_sources, wearableFallback(source)));
}
