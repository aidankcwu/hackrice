import type { EvidenceGrade, LayerName, PinGrade } from "./types";

/**
 * Display units per engine factor key — copied from `Factor.unit` in
 * lib/score/brian_score.py (`FACTORS`), which the JSON payload omits.
 */
export const FACTOR_UNITS: Record<string, string> = {
  steps: "steps",
  vilpa_min: "min/day",
  resistance_min_wk: "min/week",
  fitness_pct: "percentile",
  gait_speed: "m/s",
  sleep_hours: "h",
  sri: "SRI 0–100",
  day_light_min: "min/day",
  night_light_lux: "melanopic lx",
  social_index: "index 0–100",
  purpose: "1–6",
  nature_min_wk: "min/week",
  noise_night_db: "dB(A)",
  med_adherence: "0–1",
  alcohol_drinks: "drinks/day",
  smoker: "0/1",
  sauna_wk: "sessions/week",
  recovery_ratio: "7d/60d ln-RMSSD",
};

/** Units for the weekly ledger lines (`weekly_ledger` in the engine). */
export const LEDGER_UNITS: Record<string, string> = {
  nature_min_wk: "min",
  resistance_min_wk: "min",
  sauna_wk: "sessions",
  steps: "steps",
  day_light_min: "min/day",
  vilpa_min: "min/day",
  social_index: "index",
};

/** Human labels for evidence grades (`SHRINK` keys in the engine). */
export const GRADE_LABELS: Record<EvidenceGrade, string> = {
  A_rct: "A (randomised trial)",
  A_cohort: "A (large cohort)",
  B: "B",
  C: "C",
};

/** Collapse the engine's four grades to the three letters shown on pins and rows. */
export const gradeLetter = (grade: EvidenceGrade | PinGrade): PinGrade =>
  grade === "A_rct" || grade === "A_cohort" ? "A" : (grade as PinGrade);

/** Shrink applied to observational hazard ratios before use (engine `SHRINK`). */
export const GRADE_SHRINK: Record<EvidenceGrade, number> = {
  A_rct: 1.0,
  A_cohort: 0.7,
  B: 0.5,
  C: 0.25,
};

/** Within-layer discount on correlated factors (engine `LAYER_DISCOUNT`). */
export const LAYER_DISCOUNT = [1.0, 0.6, 0.4, 0.3, 0.25, 0.2] as const;

/**
 * Daily targets the three activity rings sweep against.
 *
 * These are conventional round numbers of the kind a fitness app ships as a
 * starting ring goal — not medical thresholds, and nothing here is claimed to
 * be research-backed. The scoring engine's own dose-response curves are the
 * only evidence-derived numbers on the dashboard; a ring at 100 % means the
 * wearer hit the target they were set, nothing more.
 *
 * `RING_GOAL_STEPS` is deliberately the same 8,000 the engine uses as the
 * under-60 step plateau, so the ring and the Movement layer don't disagree.
 */
export const RING_GOAL_MOVE_KCAL = 500;
export const RING_GOAL_STEPS = 8000;
export const RING_GOAL_EXERCISE_MIN = 30;

/** Engine layer key → display label (engine `LAYER_LABELS`). */
export const LAYER_LABELS: Record<string, LayerName> = {
  movement: "Movement",
  sleep: "Sleep",
  light: "Light & clock",
  social: "Social",
  environment: "Environment",
  diet: "Diet & substances",
  recovery: "Recovery",
};
