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

export type Provenance = "glasses" | "whoop" | "entered" | "seeded" | "imputed";

export const PROVENANCE_LABEL: Readonly<Record<Provenance, string>> = {
  glasses: "Glasses",
  whoop: "WHOOP",
  entered: "Entered",
  seeded: "Seeded",
  imputed: "Imputed",
};

/** One-line meaning for a tooltip / title. */
export const PROVENANCE_HINT: Readonly<Record<Provenance, string>> = {
  glasses: "Measured by the Ray-Ban Meta glasses (episodes from the camera pipeline)",
  whoop: "From the wearer's WHOOP data",
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

export interface ProvenanceContext {
  /** Whether the day's wearable rows are the wearer's WHOOP or the demo seed. */
  wearable: "whoop" | "seeded";
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
