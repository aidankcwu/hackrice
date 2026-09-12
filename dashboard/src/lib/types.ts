export interface Sensor { lux_proxy: number; cct: number; hist_spread: number; frame_delta: number; flow_mag: number; sharpness: number; phash: string }
export interface Device { accel_rms: number; gps_speed: number }
/** The four `ai` enums, mirroring `longevity/ai_fields.py` (and `pipeline/models.py`).
 * They are menus, not free text: long on purpose so the VLM can be specific, and
 * still one short value per field. `TickAI` keeps `string` on the wire fields so a
 * backend that has grown a value this build has not seen still renders. */
export type Scene = "home" | "office" | "classroom" | "library" | "lab" | "restaurant" | "cafe" | "bar" | "gym" | "store" | "grocery_store" | "hospital" | "hotel" | "park" | "trail" | "campus" | "street" | "parking_lot" | "beach" | "nature" | "sports_venue" | "construction_site" | "car" | "public_transit" | "airport" | "sauna" | "cold_plunge" | "indoor_other" | "outdoor_other" | "unknown";
export type Activity = "seated" | "standing" | "walking" | "running" | "cycling" | "driving" | "exercising" | "lifting_weights" | "stretching" | "eating" | "drinking" | "cooking" | "reading" | "computer_use" | "phone_use" | "talking" | "shopping" | "cleaning" | "lying_down" | "sleeping" | "personal_care" | "commuting" | "other" | "unknown";
export type FoodType = "vegetables" | "fruit" | "grains" | "beans_legumes" | "fish" | "seafood" | "poultry" | "red_meat" | "eggs" | "dairy" | "nuts" | "salad" | "sandwich" | "burger" | "pizza" | "pasta" | "rice_bowl" | "noodles" | "soup" | "wrap_taco" | "breakfast" | "processed" | "fried_food" | "fast_food" | "snack" | "chips" | "candy" | "baked_goods" | "cereal" | "protein_bar" | "sweets" | "dessert" | "bread" | "potatoes" | "mixed" | "none";
export type Drink = "none" | "water" | "coffee" | "tea" | "energy_drink" | "soda" | "alcohol" | "juice" | "smoothie" | "milk" | "sports_drink" | "boba" | "beer" | "wine" | "cocktail" | "unknown";
export interface TickAI { as_of: number; age_ms: number; scene: string; activity: string; food_present: boolean; food_type: string; caffeine_visible: boolean; alcohol_visible: boolean; screen_present: boolean; vegetation_visible: boolean; people_present: boolean; people_interacting?: boolean; direct_sunlight_visible?: boolean; outdoor_visible?: boolean; smoking_or_vaping_visible?: boolean; medication_visible?: boolean; caption?: string; objects?: string[]; drink?: Drink; conf: number }
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

// --- Judge session + recap (POST /api/session/*, POST /api/recap) ---
/** A named judging window on the pipeline's *tick* clock, not wall time. */
export interface Session { id: string; name: string; started_t: number; ended_t: number | null }
/** One key moment: a decision that saved a frame. `frame_url` is server-built — don't assemble it. */
export interface Moment { decision_id: string; t: number; trigger: string; category: string; severity: string; caption: string; frame_ref: string; frame_url: string; sharpness?: number | null; blurry?: boolean; insight?: string | null }
export interface Subscore { metric: string; layer: string; label: string; value: string | number | null; unit: string; score: number; target: string; source: "live" | "seeded"; grade: string; note?: string | null }
export interface RecapNarrative { headline: string; paragraphs: string[]; suggestions: string[]; spoken: string }
/** `session.id` is null for a bare time-window recap. `spoken` is whether it actually reached the glasses. */
export interface Recap {
  id: string;
  session: { id: string | null; name: string | null; from_t: number; to_t: number; duration_s: number; tick_count: number; ai_coverage: number; decision_count: number };
  score: { overall: number; subscores: Subscore[] };
  moments: Moment[];
  narrative: RecapNarrative;
  spoken: boolean;
  generated_at: number;
  model: string;
}
