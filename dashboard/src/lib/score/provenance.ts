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

export type Provenance = "glasses" | "whoop" | "fitbit" | "entered" | "seeded" | "imputed";

export const PROVENANCE_LABEL: Readonly<Record<Provenance, string>> = {
  glasses: "Glasses",
  whoop: "WHOOP",
  fitbit: "Fitbit",
  entered: "Entered",
  seeded: "Seeded",
  imputed: "Imputed",
};

/** One-line meaning for a tooltip / title. */
export const PROVENANCE_HINT: Readonly<Record<Provenance, string>> = {
  glasses: "Measured by the Ray-Ban Meta glasses (episodes from the camera pipeline)",
  whoop: "From the wearer's WHOOP data",
  fitbit: "From the wearer's Fitbit, via the Google Health API",
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
export const LIVE_SOURCES: ReadonlySet<string> = new Set(["fitbit", "apple_watch_live", "whoop_live"]);

export interface ProvenanceContext {
  /** Whether the day's wearable rows are the wearer's own device or the demo seed. */
  wearable: "whoop" | "fitbit" | "seeded";
}

/** The engine's per-factor provenance, as the backend adapter reports it. */
export interface EngineProvenance {
  source?: string;
  basis?: string;
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
