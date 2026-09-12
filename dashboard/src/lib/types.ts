export interface Sensor { lux_proxy: number; cct: number; hist_spread: number; frame_delta: number; flow_mag: number; sharpness: number; phash: string }
export interface Device { accel_rms: number; gps_speed: number }
export interface TickAI { as_of: number; age_ms: number; scene: string; activity: string; food_present: boolean; food_type: string; caffeine_visible: boolean; alcohol_visible: boolean; screen_present: boolean; vegetation_visible: boolean; people_present: boolean; conf: number }
export interface Tick { v: number; tick_id: string; t: number; seq: number; sensor: Sensor; device?: Device; ai?: TickAI; frame_ref?: string }
export type DecisionAction =
  | { type: "annotate"; line: string }
  | { type: "log_insight"; category: string; text: string }
  | { type: "watch"; after_s: number; reason: string }
  | { type: "speak"; text: string; urgency: "low" | "normal" | "high" }
  | { type: "nothing" };
export interface Decision { id: string; t: number; trigger: string; trigger_tick_id: string; episode_id: string | null; interpretation: string; confidence: number; actions: DecisionAction[]; spoke: boolean; dropped: boolean; drop_reason: string | null; latency_ms: number; model: string }
/** `tick_interval_s` is the capture cadence in seconds (1.5 off the glasses); absent on older backends, treat as 1. */
export interface Status { demo_mode: boolean; source: string; uptime_s: number; tick_count: number; ai_coverage: number; t1_busy: boolean; dropped_escalations: number; last_tick_t: number; tick_interval_s?: number }
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
