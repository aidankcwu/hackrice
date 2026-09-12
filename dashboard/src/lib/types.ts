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
/** One long-format row of `GET /api/seeded` — the shape the backend actually
 * stores (`pipeline.models.SeededRow`), before `api.seeded` pivots it into a
 * `SeededDay`. `source` is the device that produced the row: a real wearable
 * name (`fitbit`) when a connected device backfilled the day, one of the demo
 * devices (`whoop`, `oura`, `apple_watch`) when it is seeded. */
export interface SeededMetricRow { day: string; metric: string; value: number; unit?: string; source?: string }
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
