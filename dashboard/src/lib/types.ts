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
export type AnswerKind = "yes_no" | "count" | "free";
export type AnswerFills = "confirmed" | "count" | "food_type" | "note";
export type DecisionAction =
  | { type: "annotate"; line: string }
  | { type: "log_insight"; category: string; text: string }
  | { type: "watch"; after_s: number; reason: string }
  | { type: "speak"; text: string; urgency: "low" | "normal" | "high"; outcome?: string | null }
  /** A question for the wearer (docs/ASK_DESIGN.md §5). The reasoner proposes
   *  `text`/`answer_kind`/`fills`/`reason`; the QuestionManager stamps
   *  `question_id` and `outcome` ("sent" | "suppressed:<reason>") onto the row
   *  it stores, so a decision card can say what actually happened to the ask. */
  | { type: "ask"; text: string; answer_kind?: AnswerKind; fills?: AnswerFills; reason?: string; question_id?: string | null; outcome?: string | null }
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

// --- Ask / answer (GET /api/questions, POST /api/answer, POST /api/ask) ---
/** What `QuestionManager` made of the transcript. Every field is optional: the
 *  backend stores `{}` for a question nobody answered, and only fills the keys
 *  the answer actually settled. */
export interface ParsedAnswer {
  understood?: boolean | null;
  confirmed?: boolean | null;
  count?: number | null;
  food_type?: string | null;
  note?: string | null;
  followup?: string | null;
}
export type QuestionStatus = "open" | "answered" | "expired" | "suppressed";
/** One `pending_questions` row (docs/API.md "Ask / answer"). `suppressed_reason`
 *  names the guard that said no: `ask_unsupported`, `no_transport`, `one_open`,
 *  `same_episode`, `ask_min_gap`, `ask_max_per_hour`, `speech_gap`, `send_failed`. */
export interface Question {
  id: string;
  created_t: number;
  expires_t: number | null;
  decision_id: string | null;
  episode_id: string | null;
  question: string;
  answer_kind: AnswerKind;
  fills: AnswerFills;
  status: QuestionStatus;
  answer_text: string | null;
  answer_t: number | null;
  heard: boolean | null;
  parsed: ParsedAnswer | null;
  followup_of: string | null;
  sent_t: number | null;
  suppressed_reason: string | null;
}
export interface AnswerResult { question_id: string; accepted: boolean }
/** `question_id` is null when a guard suppressed the ask; `suppressed_reason` says which. */
export interface AskResult { question_id: string | null; suppressed_reason: string | null }

// --- Voice-agent conversations (docs/CONVERSATION_DESIGN.md) ---
export interface ConversationTurn {
  t: number;
  role: "agent" | "wearer";
  text: string;
  kind?: "question" | "statement";
  heard?: boolean;
}
export interface ConversationSettled {
  confirmed: boolean | null;
  count: number | null;
  food_type: string | null;
  note: string | null;
}
export interface Conversation {
  id: string;
  opened_t: number;
  closed_t: number | null;
  reason: string;
  topic: string;
  decision_id: string | null;
  episode_id: string | null;
  state: "active" | "closed";
  turns: ConversationTurn[];
  settled: ConversationSettled | null;
  close_reason: string | null;
}
export interface OpenConversationResult { id: string | null; reason?: string }
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

// --- The persona that grows (GET/PUT /api/persona, GET/DELETE /api/profile) ---
/** The persona T1 is briefed with. `source` is `custom` once an operator has
 *  stored an override, `default` while it is the one compiled into the backend. */
export interface Persona { text: string; source: "default" | "custom" }
/** One durable fact `remember` learned about the wearer. Read back into every
 *  system prompt, so it outlives today's summary. */
export interface ProfileLine { id: string; t: number; line: string; source_decision_id: string | null }
export interface ForgetResult { id: string; removed: boolean }

/** One row of the Logs index — GET /api/recaps. No body: see RecapStore.list. */
export interface RecapSummary { id: string; session_id: string | null; from_t: number; to_t: number; generated_at: number; duration_s: number }

// --- The protocol (docs/API.md "The protocol", PLAN 2.1) ---
/** `days` are weekdays, 0 = Monday (Python `weekday()`); windows are local "HH:MM". */
export type ProtocolKind = "dose" | "meal" | "winddown" | "walk";
export type ProtocolStatus = "waiting" | "seen" | "done" | "missed" | "undone";
export interface ProtocolItem { id: string; name: string; kind: ProtocolKind; window_start: string; window_end: string; days: number[]; created_t: number }
/** GET /api/protocol/today: the items scheduled today, each with today's status. */
export interface ProtocolTodayItem extends ProtocolItem { status: ProtocolStatus; seen_t: number | null; evidence_ref: string | null; updated_t: number | null }
export interface ProtocolToday { day: string; items: ProtocolTodayItem[] }
/** One row of GET /api/protocol/export.csv: one item on one local day. Empty cells are null. */
export interface ProtocolDayRow { day: string; item_id: string; name: string; kind: string; window_start: string; window_end: string; status: string; seen_t: number | null; evidence_ref: string | null; updated_t: number | null }
