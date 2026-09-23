/**
 * The contract between three things:
 *
 *   1. the backend's `GET /api/healthspan?days=N` (backend/pipeline/scoring/
 *      healthspan.py over the same `brian_score` engine) — `HealthspanWeek`, a
 *      full `EnginePayload` for today plus per-day hours, with the backend's own
 *      coverage rule and per-factor provenance already applied;
 *   2. `shape.ts`, which turns that payload plus the day inputs `backend.ts`
 *      reads (seeded rows, episodes) into `DashboardData`;
 *   3. the UI (`src/components/brian/BrianDashboard.tsx`) which reads
 *      `DashboardData` and nothing else.
 *
 * `EngineRequest` is still the body `POST /api/score` hands the local engine
 * (`run_from_json`) — the brief's contract — but no dashboard number comes from it.
 * Field names on the engine side mirror the Python exactly (snake_case).
 */
import type { GlassesCoverage, ObsProvenance, Provenance } from "./provenance";

// ---------------------------------------------------------------------------
// Engine request
// ---------------------------------------------------------------------------

/** `Profile.goal` in brian_score.py. */
export type Goal = "average" | "athlete" | "shift" | "genetic_risk";

export const GOALS: ReadonlyArray<{ value: Goal; label: string }> = [
  { value: "average", label: "Average" },
  { value: "athlete", label: "Athlete" },
  { value: "shift", label: "Shift worker" },
  { value: "genetic_risk", label: "Genetic risk" },
];

export interface EngineProfile {
  age?: number;
  sex?: "M" | "F";
  goal?: Goal;
  apoe4?: boolean;
  lpa_high?: boolean;
  cyp1a2_slow?: boolean;
  /** Habitual bedtime, decimal hours after local midnight (23.5 = 23:30). */
  bedtime_hh?: number;
}

/**
 * Episode vocabulary the engine understands (`observations_from_app`,
 * `pins_from_episodes`). The pipeline's `gym_session` is passed through too —
 * the engine ignores it as an episode; the adapter turns it into
 * `wearable_day.workouts` strength minutes.
 */
export type EngineEpisodeType =
  | "screen_block"
  | "caffeine_sighting"
  | "meal"
  | "conversation"
  | "outdoor_block"
  | "alcohol_sighting"
  | "walk"
  | "sauna"
  | "gym_session";

export interface EngineEpisode {
  type: EngineEpisodeType;
  /** Local decimal hour the episode started (12.1 = 12:06). */
  start_hh: number;
  minutes: number;
  /** Scene tag from the T0 classifier (`park`, `office`, …). */
  scene?: string;
  /** Melanopic-ish lux at the eye. Leave undefined when only a relative proxy exists. */
  lux?: number;
  /** People in a conversation. */
  people?: number;
  people_ids?: string[];
  /** Steps per minute, walks only. */
  cadence?: number;
  /** Sound level, dB(A). */
  db?: number;
  /** Meal tags; `mediterranean` / `plant` count toward the diet pattern. */
  tags?: string[];
  /** Meal label shown on the pin. */
  label?: string;
  /** Drinks in an alcohol sighting (defaults to 1). */
  count?: number;
  /** A captured frame for the evidence pin, or null when none survived. */
  frame_url?: string | null;
  /** Pass-through for the UI; ignored by the engine. */
  id?: string;
  kind?: string;
}

export interface EngineWorkout {
  kind: "strength" | "cardio" | "mobility" | string;
  minutes: number;
}

export interface EngineWearableDay {
  steps?: number;
  vilpa_min?: number;
  sleep_hours?: number;
  /** Sleep regularity index 0–100. */
  sri?: number;
  /** 7-day / 60-day ln-RMSSD ratio. */
  hrv_ratio?: number;
  strain?: number;
  workouts?: EngineWorkout[];
  night_db?: number;
  night_lux?: number;
  vo2max_pct?: number;
  height_m?: number;
  purpose?: number;
  smoker?: 0 | 1;
  /** Habitual sleep length the forecast starts from. */
  baseline_sleep_h?: number;
  /** Tonight's bedtime minus habit, minutes (positive = later). */
  planned_bed_shift_min?: number;
}

export interface EngineWeekRow {
  nature_min?: number;
  steps?: number;
  day_light_min?: number;
  vilpa_min?: number;
  social_index?: number;
  workouts?: EngineWorkout[];
  sauna?: 0 | 1;
}

export interface EngineHistory {
  exposure: number[];
  outcome: number[];
  /** One covariate column, or a row-major matrix. */
  covariates?: number[] | number[][];
  weekday?: number[];
  exposure_name?: string;
  outcome_name?: string;
  prior_beta?: number;
  prior_se?: number;
}

export interface EngineRequest {
  profile?: EngineProfile;
  episodes?: EngineEpisode[];
  wearable_day?: EngineWearableDay;
  /** Earlier days of this week, oldest first (today is *not* included). */
  week_rows?: EngineWeekRow[];
  history?: EngineHistory;
}

// ---------------------------------------------------------------------------
// Engine payload (run_from_json output)
// ---------------------------------------------------------------------------

export type EvidenceGrade = "A_rct" | "A_cohort" | "B" | "C";
export type PinGrade = "A" | "B" | "C";

/** The engine's own layer labels (`LAYER_LABELS` in brian_score.py). The nine
 *  rows the UI draws are a re-cut of these — see `LAYER_SPECS` in Layers.tsx. */
export type LayerName =
  | "Movement"
  | "Sleep"
  | "Light & clock"
  | "Social"
  | "Environment"
  | "Diet & substances"
  | "Cognition"
  | "Recovery";

export const LAYER_ORDER: readonly LayerName[] = [
  "Movement",
  "Sleep",
  "Light & clock",
  "Social",
  "Environment",
  "Diet & substances",
  "Cognition",
  "Recovery",
];

export interface EngineFactor {
  key: string;
  layer: LayerName;
  label: string;
  dose: number | null;
  hr: number | null;
  /** Healthy-life hours credited (+) or debited (−) today vs a typical person. */
  hours: number;
  grade: EvidenceGrade;
  measured: boolean;
  source: string;
  /** The backend adapter's provenance for the dose (`/api/healthspan` factors only). */
  provenance?: ObsProvenance["source"];
  basis?: string;
  detail?: string;
}

export type LedgerStatus = "on_track" | "at_risk" | "behind";

export interface EngineLedgerLine {
  key: string;
  label: string;
  accrued: number;
  target: number;
  projected: number;
  deficit: number;
  days_elapsed: number;
  status: LedgerStatus;
}

export interface EngineForecast {
  sleep_hours: number;
  hrv_change_pct: number;
  sri_change_pts: number;
  melatonin_delay_min: number;
  drivers: string[];
}

export interface EngineLever {
  key: string;
  label: string;
  action: string;
  hours_gain: number;
  time_min: number;
  roi_hours_per_min: number;
  layers: string[];
  source: string;
}

export interface EngineInsight {
  kind: "tonight" | "today" | "week" | "lever" | "you";
  text: string;
  source: string;
}

export interface EnginePin {
  /** `HH:MM` */
  time: string;
  img: string | null;
  grade: PinGrade;
  kind: "credit" | "debit";
  seen: string;
  effect: string;
}

export interface EngineEffect {
  exposure: string;
  outcome: string;
  /** null when fewer than 14 days (Python emits NaN; the runner maps it to null). */
  beta: number | null;
  ci: [number | null, number | null];
  n: number;
  blended_beta: number;
  note: string;
}

export interface EnginePayload {
  overall: number;
  layers: Record<LayerName, number>;
  years_delta: number;
  years_ci: [number, number];
  hours_today: number;
  hours_ci: [number, number];
  factors: EngineFactor[];
  ledger: EngineLedgerLine[];
  forecast: EngineForecast;
  levers: EngineLever[];
  insights: EngineInsight[];
  pins: EnginePin[];
  effects: EngineEffect[];
  observations: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Backend healthspan payload (GET /api/healthspan, docs/API.md)
// ---------------------------------------------------------------------------

/**
 * The full `/api/healthspan` payload: the engine's `to_payload` keys (the
 * `EnginePayload` fields shape.ts reads — same names, no renames) plus the
 * adapter's own. Only the adapter keys the dashboard reads are typed here.
 */
export interface HealthspanPayload extends EnginePayload {
  /** ISO date scored. */
  day: string;
  /**
   * The profile the engine ran with (`PROFILE_AGE` / `PROFILE_SEX`, the request's
   * goal, the day's bedtime); `bedtime_source` is `missing` when 23:00 was assumed.
   */
  profile: { age: number; sex: string; goal: Goal; bedtime_hh: number; bedtime_source: string };
  /** Per observation key: which stream filed it, or `missing` (imputed, earns nothing). */
  provenance: Record<string, ObsProvenance>;
  /** `factor_days` is the trailing week; `uncovered_days` those with no glasses episode. */
  window: { factor_days: string[]; uncovered_days: string[] };
}

/** One trailing day as `?days=N` returns it: no factors, pins or provenance. */
export interface HealthspanLiteDay {
  day: string;
  hours_today: number;
}

/** `GET /api/healthspan?days=N`: per-day hours oldest first, and today's full payload. */
export interface HealthspanWeek {
  day: string;
  days: HealthspanLiteDay[];
  today: HealthspanPayload;
}

// ---------------------------------------------------------------------------
// Day inputs (one per calendar day, from the pipeline's own store)
// ---------------------------------------------------------------------------

/** A pipeline episode as `GET /api/episodes` returns it (see backend models.Episode). */
export interface PipelineEpisode {
  id: string;
  kind: string;
  start_t: number;
  end_t: number | null;
  duration_s: number;
  /** Dominant T0 tags, e.g. `{scene: "park", activity: "walking", food_type: "none"}`. */
  dominant: Record<string, unknown>;
  tick_count?: number;
  open: boolean;
}

/**
 * One day's raw rows for the seven-day table and the adherence log. `seeded` is
 * the pivot of the long-format `GET /api/seeded` rows for that day
 * (`metric -> value`), keys as in backend/pipeline/seed/fixtures.py
 * (`sleep_hours`, `bed_time`, `hrv_rmssd_ratio`, `sleep_regularity_sri`,
 * `steps`, `vilpa_minutes`, `strain`, `night_noise_db`, `recovery_score`,
 * `resting_hr`, `journal_caffeine_late`, `journal_alcohol`, …).
 */
export interface DayInputs {
  /** ISO date, local. */
  date: string;
  episodes: PipelineEpisode[];
  seeded: Record<string, number>;
  isToday: boolean;
  /** "Now" on the tick clock (unix seconds) — closes open episodes and dates the payload. */
  nowT: number;
}

// ---------------------------------------------------------------------------
// Dashboard data (what BrianDashboard renders)
// ---------------------------------------------------------------------------

/**
 * Lucide icon names the UI knows how to draw; keeps icons out of the data layer.
 * The per-layer assignment is fixed by the SKILL.md token table — Clock=clock,
 * Light=sun, People=users, Outside=trees, Air=wind, Mind=brain, Body=footprints,
 * Sleep=moon, Fuel=utensils, Recovery=flame.
 */
export type IconName =
  | "clock"
  | "sun"
  | "users"
  | "footprints"
  | "moon"
  | "trees"
  | "wind"
  | "brain"
  | "wine"
  | "coffee"
  | "smartphone"
  | "flame"
  | "activity"
  | "utensils"
  | "dumbbell"
  | "eye";

export interface Person {
  name: string;
  age: number;
  sex: "M" | "F";
  goal: Goal;
  /** Human label for `goal` ("Average", "Athlete", …). */
  profileLabel: string;
  /** e.g. "Ray-Ban Meta + WHOOP" */
  device: string;
  bedtime_hh: number;
}

export interface LayerRow {
  name: LayerName;
  icon: IconName;
  /** 0–100 on the evidence-derived best/worst range. */
  score: number;
  /** Discounted sum of the layer's factor hours; the seven rows sum to `hours_today`. */
  hours: number;
  /** One line of measured doses, e.g. "9,100 steps · 2 min hard effort". */
  note: string;
  /** How many of the layer's factors were actually measured today. */
  measured: number;
  total: number;
}

export interface PinRow {
  id: string;
  time: string;
  seen: string;
  kind: "earn" | "cost";
  effect: string;
  grade: PinGrade;
  img: string | null;
  icon: IconName;
}

export interface ForecastView extends EngineForecast {
  /** What still recovers tonight, written from the drivers (never hardcoded). */
  fix: string;
  /** Habitual bedtime as `HH:MM`. */
  bedtime: string;
}

export interface LeverRow {
  key: string;
  action: string;
  /** Healthy-life hours gained. */
  gain: number;
  /** Minutes it costs; 0 = costs no time. */
  time: number;
  layers: string[];
  source: string;
}

export interface LedgerRow extends EngineLedgerLine {
  /** Display unit for the numbers ("min", "min/day", "sessions", "steps", "index"). */
  unit: string;
}

export interface EffectRow {
  exposure: string;
  outcome: string;
  /** Formatted estimate, e.g. "+0.4% per 10 min" or "not yet separable from zero". */
  beta: string;
  /** Raw CI bounds in outcome units; null when not estimable. */
  lo: number | null;
  hi: number | null;
  n: number;
  /** True once 14+ days exist and the estimate is separable from zero. */
  ok: boolean;
  note: string;
}

export interface WeekDay {
  /** Short weekday, "Sat". */
  day: string;
  /** ISO date, "2026-09-06". */
  date: string;
  /** Bedtime "HH:MM" or "—". */
  bed: string;
  sleep: number | null;
  /** HRV ratio vs baseline (1.02 = +2%). */
  hrv: number | null;
  /** Recovery score 0–100. */
  rec: number | null;
  rhr: number | null;
  steps: number | null;
  sri: number | null;
  /** Healthy-life hours that day from a full engine run on that day's data. */
  hours: number | null;
  /** Comma-joined tags: "caffeine, alcohol". */
  tag: string;
  today: boolean;
}

/**
 * `utility_today()` from the engine — how well today was lived, not how long.
 * Every component is null until something real measured it: a day with no PVT,
 * no self-check and no recovery score has no utility at all and the whole object
 * is absent rather than defaulted to a perfect day (R1).
 */
export interface ExperienceView {
  /** 0–1. */
  utility: number;
  /** `utility` × 24, the "fully-lived hours" currency. */
  fully_lived_hours: number;
  components: {
    /** Reaction time from the PVT. */
    pvt: number | null;
    /** The three-tap energy / mood / clarity check. */
    check: number | null;
    /** WHOOP recovery score 0–100. */
    recovery: number | null;
    pain_or_illness: boolean;
  };
}

/** `two_currencies()` — today's fully-lived hours and the future healthy years they weight. */
export interface CurrenciesView {
  future_healthy_years: number;
  future_healthy_years_ci: [number, number];
  fully_lived_hours_today: number;
  utility_today: number;
  /** Null until enough prior days carry a utility; a 30-day label on 6 days is a lie. */
  utility_mean_30d: number | null;
  /** How many days actually went into the mean. */
  utility_days: number;
}

export interface DataSource {
  /** Always "live": the loader has no offline fixture (loader.ts), so there is no other mode. */
  mode: "live";
  api_base: string;
  /** The day being scored, ISO. */
  day: string;
  tick_count?: number;
  capture_source?: string;
  demo_mode?: boolean;
  last_tick_t?: number;
  /** Today's `seeded` rows by metric -> the `source` that wrote each (`fitbit`, `whoop`, `phone`...). */
  wearable_sources?: Record<string, string>;
  /** Whether the glasses filed any episode today / this week; `shapeDashboard` sets it from the backend's `window`. */
  glasses_coverage?: GlassesCoverage;
  /** The backend's per-key provenance for the scored day; every chip reads it where it has a row (provenance.ts). */
  provenance?: Record<string, ObsProvenance>;
}

/**
 * One compact stat under "The numbers your wearable already knows" (§1.8).
 * `value` is null whenever no real row reported it today, and the tile then
 * prints the voice.md unmeasured string instead of a zero (R1).
 */
export interface WearableStat {
  key: string;
  /** "Steps", "Strain", "Resting heart rate", … */
  label: string;
  value: number | null;
  /** "steps", "h", "bpm", "%", "°C", "br/min" — empty for an index. */
  unit: string;
  /** Decimals to print; 0 for steps and bpm, 1 for hours and temperature. */
  digits: number;
  /** Which stream filed it — the chip on the tile. */
  provenance: Provenance;
}

export interface DashboardData {
  /** Unix seconds when this payload was built. */
  generated_at: number;
  /** Always carries `glasses_coverage`: a glasses zero is gated on it (provenance.ts `glassesGap`). */
  source: DataSource & { glasses_coverage: GlassesCoverage };
  person: Person;
  overall: number;
  hours_today: number;
  hours_ci: [number, number];
  years_delta: number;
  years_ci: [number, number];
  layers: LayerRow[];
  /**
   * How well today was lived (`utility_today`), and the two currencies the
   * ledger card shows. Both absent until the loader lane forwards the engine's
   * `experience` / `currencies` — and absent is the honest state for a day with
   * no PVT, no self-check and no recovery score.
   */
  experience?: ExperienceView | null;
  currencies?: CurrenciesView | null;
  /** The wearable-known numbers for §1.8; absent until the loader lane fills it. */
  wearable?: WearableStat[];
  pins: PinRow[];
  forecast: ForecastView;
  levers: LeverRow[];
  ledger: LedgerRow[];
  effects: EffectRow[];
  week: WeekDay[];
  /** One data-derived sentence under the seven-day table. */
  week_summary: string;
  /** Judge-facing evidence: every factor with dose, HR, hours, grade and source. */
  factors: EngineFactor[];
  insights: EngineInsight[];
  observations: Record<string, number>;
  /** Wall time of the backend's `/api/healthspan` round trip (it runs the engine), for the "<1.5 s" check. */
  engine_ms: number;
}
