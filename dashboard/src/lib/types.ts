export interface Sensor { lux_proxy: number; cct: number; hist_spread: number; frame_delta: number; flow_mag: number; sharpness: number; phash: string }
export interface Device { accel_rms: number; gps_speed: number }
/** The four `ai` enums, mirroring `longevity/ai_fields.py` (and `pipeline/models.py`).
 * They are menus, not free text: long on purpose so the VLM can be specific, and
 * still one short value per field. `TickAI` keeps `string` on the wire fields so a
 * backend that has grown a value this build has not seen still renders. */
export type Scene = "home" | "office" | "restaurant" | "gym" | "sauna" | "cold_plunge" | "park" | "trail" | "vehicle" | "street" | "kitchen" | "bedroom" | "living_room" | "bathroom" | "dorm_room" | "classroom" | "lecture_hall" | "library" | "lab" | "cafe" | "bar" | "grocery_store" | "store" | "campus_outdoor" | "backyard" | "beach" | "parking_lot" | "stadium" | "hallway" | "elevator" | "transit" | "unknown";
export type Activity = "seated" | "standing" | "walking" | "exercising" | "eating" | "lying_down" | "cooking" | "reading" | "typing" | "phone_use" | "talking" | "driving" | "running" | "lifting_weights" | "stretching" | "cycling" | "cleaning" | "shopping" | "drinking" | "unknown";
export type FoodType = "vegetables" | "fruit" | "grains" | "fish" | "poultry" | "red_meat" | "processed" | "sweets" | "mixed" | "salad" | "sandwich" | "burger" | "pizza" | "pasta" | "rice_bowl" | "noodles" | "soup" | "eggs" | "dairy" | "nuts" | "chips" | "candy" | "baked_goods" | "cereal" | "protein_bar" | "fast_food" | "dessert" | "none";
export type Drink = "none" | "water" | "coffee" | "tea" | "energy_drink" | "soda" | "alcohol" | "juice" | "smoothie" | "milk" | "sports_drink" | "boba" | "beer" | "wine" | "cocktail" | "unknown";
export interface TickAI { as_of: number; age_ms: number; scene: string; activity: string; food_present: boolean; food_type: string; caffeine_visible: boolean; alcohol_visible: boolean; screen_present: boolean; vegetation_visible: boolean; people_present: boolean; caption?: string; objects?: string[]; drink?: Drink; conf: number }
export interface Tick { v: number; tick_id: string; t: number; seq: number; sensor: Sensor; device?: Device; ai?: TickAI; frame_ref?: string }
export type DecisionAction =
  | { type: "annotate"; line: string }
  | { type: "log_insight"; category: string; text: string }
  | { type: "watch"; after_s: number; reason: string }
  | { type: "speak"; text: string; urgency: "low" | "normal" | "high" }
  | { type: "nothing" };
export interface Decision { id: string; t: number; trigger: string; trigger_tick_id: string; episode_id: string | null; interpretation: string; confidence: number; actions: DecisionAction[]; spoke: boolean; dropped: boolean; drop_reason: string | null; latency_ms: number; model: string }
export type CaptureStats = Record<string, unknown> & {
  loop?: string | Record<string, unknown>;
  tagger?: string | Record<string, unknown>;
  phone?: Record<string, unknown>;
  link?: Record<string, unknown>;
  frames?: Record<string, unknown>;
  ring?: Record<string, unknown>;
  converted?: number;
  dropped?: number;
};
/** `tick_interval_s` is the capture cadence in seconds (1.5 off the glasses); absent on older backends. */
export interface Status { demo_mode: boolean; source: string; uptime_s: number; tick_count: number; ai_coverage: number; t1_busy: boolean; dropped_escalations: number; last_tick_t: number; tick_interval_s?: number; capture?: CaptureStats }
export interface Episode { id: string; kind: string; start_t: number; end_t: number | null; duration_s: number; open: boolean; label?: string }
export interface Insight { id: string; t: number; category: string; text: string }
export interface MetricScore { id: string; layer: string; metric: string; source: "live" | "seeded"; grade: "A" | "B" | "C"; target: string; value: string | number; score: number }
export interface Scores { overall: number; metrics: MetricScore[] }
export interface PendingCheck { id: string; due_t: number; reason: string; trigger?: string }
export interface TodaySummary { lines: string[] }
export interface SeededDay { date: string; sleep_h: number; hrv_ratio: number; steps: number; sri: number; caffeine_last?: string; recovery?: number; resting_hr?: number; run_km?: number; journal?: string; sources?: Record<string, string> }
export type BiometricOrigin = "seed" | "live";
export interface Biometrics { metric: string; source: string; origin?: BiometricOrigin; points: [number, number][] }
/** GET /api/biometrics?metrics=a,b,c — one round trip for the whole strip. */
export interface BiometricSeries { source: string; origin: BiometricOrigin; points: [number, number][] }
export interface BiometricsMulti { series: Record<string, BiometricSeries> }
export interface WearableMetricRow { metric: string; source: string; origin: BiometricOrigin; count: number; last_t: number }
export interface WearableMetricInfo { unit: string; devices: string[]; cadence_s: number; label: string }
/** GET /api/wearables/status — what is stored, and whether a device is pushing. */
export interface WearablesStatus { metrics: WearableMetricRow[]; live_connected: boolean; live_devices: string[]; catalogue: Record<string, WearableMetricInfo> }
/** Where a healthspan number came from: `live` = episodes summed as-is, `seeded` = integration row as-is, `derived` = a proxy or conversion of either, `missing` = imputed at the population reference and never credited. */
export type Provenance = "live" | "seeded" | "derived" | "missing";
export interface HealthspanFactor { key: string; layer: string; label: string; dose: number | null; hr: number | null; hours: number; grade: string; measured: boolean; source: string; provenance: Provenance; basis: string; detail: string }
export interface HealthspanLever { key: string; label: string; action: string; hours_gain: number; time_min: number; roi_hours_per_min: number; layers: string[]; source: string }
export interface HealthspanForecast { sleep_hours: number; hrv_change_pct: number; sri_change_pts: number; melatonin_delay_min: number; drivers: string[] }
export interface HealthspanLedgerLine { key: string; label: string; accrued: number; target: number; projected: number; deficit: number; days_elapsed: number; status: "on_track" | "at_risk" | "behind" }
export interface HealthspanInsight { kind: "tonight" | "today" | "week" | "lever" | "you"; text: string; source: string }
export interface HealthspanPin { time: string; img: string | null; grade: string; kind: "credit" | "debit"; seen: string; effect: string }
export interface HealthspanEffect { exposure: string; outcome: string; beta: number | null; ci: [number | null, number | null]; n: number; blended_beta: number; note: string }
export interface HealthspanProvenance { source: Provenance; basis: string; detail: string }
export interface HealthspanProfile { age: number; sex: string; goal: string; cyp1a2_slow: boolean; height_m: number | null; bedtime_hh: number; bedtime_source: Provenance }
/** GET /api/healthspan — brian_score.to_payload plus adapter fields (backend/pipeline/scoring/healthspan.py). Sits beside, not instead of, Scores. */
export interface Healthspan { day: string; as_of_hh: number | null; engine: string; overall: number; layers: Record<string, number>; years_delta: number; years_ci: [number, number]; hours_today: number; hours_ci: [number, number]; measured: { count: number; total: number }; factors: HealthspanFactor[]; ledger: HealthspanLedgerLine[]; forecast: HealthspanForecast; levers: HealthspanLever[]; levers_free: HealthspanLever[]; insights: HealthspanInsight[]; pins: HealthspanPin[]; observations: Record<string, number>; provenance: Record<string, HealthspanProvenance>; effects: HealthspanEffect[]; profile: HealthspanProfile; baseline_sleep_h: number; window: { ledger_days: string[]; factor_days: string[]; uncovered_days: string[]; days_elapsed: number }; conventions: string[] }
