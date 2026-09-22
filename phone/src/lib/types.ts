/**
 * Wire types for the backend routes the phone reads, field for field from the
 * captures in `phone/fixtures/` (docs/STATE.md §4). Names stay snake_case, as on
 * the wire. Fields no screen reads are typed `unknown`; the phone only shows
 * what the backend decided (IOS_SPEC "Data layer").
 */

/** `/api/session/current`, and `session` inside `/api/status`. */
export interface Session {
  id: string;
  name: string;
  /** Epoch seconds on the backend's clock (simulated under `--source sim`, not the phone's). */
  started_t: number;
  ended_t: number | null;
  /**
   * Seconds watched so far, by the backend's clock. "Watching N min" reads this, never
   * `Date.now() - started_t`. Only `/api/status` carries it; `/api/session/current` does not.
   */
  elapsed_s?: number;
}

/** `health.phone` inside `/api/status`: the glasses app's socket, `null` when no capture link exists. */
export interface PhoneLink {
  /** Open sockets from the glasses app; 0 when it is not connected. */
  connected: number;
  /** Seconds since the last frame arrived, `null` before the first one. */
  latest_age_s?: number | null;
  connected_for_s?: number | null;
}

/** `GET /api/status` (fixture: `status.json`). */
export interface Status {
  demo_mode: boolean;
  /** Where frames come from: `"sim"` in the fixture, the glasses when live. */
  source: string;
  uptime_s: number;
  /** Climbs while frames arrive; a stalled count means the glasses are off. */
  tick_count: number;
  ai_coverage: number;
  t1_busy: boolean;
  dropped_escalations: number;
  last_tick_t: number | null;
  reasoner_mode: string;
  speed: number;
  tick_interval_s: number;
  gate: unknown;
  questions: {
    open: number;
    asked: number;
    answered: number;
    expired: number;
    suppressed: number;
    last_ask_t: number | null;
  };
  conversation: unknown;
  speech_spoken: number;
  health: {
    phone: PhoneLink | null;
    t0: unknown;
    t1: unknown;
    speech: unknown;
    ok: boolean;
    /** `phone_disconnected`, `no_packets_10s`, `no_ticks_60s`, `ai_coverage_low`, `vlm_errors`, `t1_errors`, `tts_failing`. */
    problems: string[];
  };
  /** The open session, `null` when none. Absent from a backend older than sessions. */
  session?: Session | null;
}

/** Where a number came from. `missing` is unmeasured: scored at the reference, earns nothing. */
export type ProvenanceSource = "live" | "seeded" | "derived" | "missing";

export interface Provenance {
  source: ProvenanceSource;
  /** The device or input behind it: `glasses`, `whoop`, `apple_watch`, `phone`, `user`, … */
  basis: string;
  detail: string;
}

export interface HealthspanFactor {
  key: string;
  layer: string;
  label: string;
  dose: number | null;
  hr: number | null;
  hours: number;
  grade: string;
  measured: boolean;
  source: string;
  provenance: ProvenanceSource;
  basis: string;
  detail: string;
}

/** `GET /api/healthspan` (fixture: `today_healthspan.json`). */
export interface Healthspan {
  /** Healthspan score, 0–100. */
  overall: number;
  layers: Record<string, number>;
  years_delta: number;
  years_ci: [number, number];
  /** Healthy-life hours earned (+) or cost (−) today. */
  hours_today: number;
  hours_ci: [number, number];
  factors: HealthspanFactor[];
  /** ISO date the payload scores, e.g. `"2026-09-22"`. */
  day: string;
  as_of_hh: number;
  engine: string;
  measured: { count: number; total: number };
  provenance: Record<string, Provenance>;
  ledger: unknown[];
  forecast: unknown;
  levers: unknown[];
  levers_free: unknown[];
  levers_personalized: unknown;
  insights: unknown[];
  pins: unknown[];
  pins_total: number;
  experience: unknown;
  currencies: unknown;
  week_table: unknown[];
  annotations: unknown[];
  narrator_prompts: unknown[];
  driver_rules: unknown;
  observations: unknown;
  effects: unknown[];
  profile: unknown;
  baseline_sleep_h: number;
  window: unknown;
  conventions: unknown[];
}

/** One item of `GET /api/episodes` (fixture: `today_episodes.json`). */
export interface Episode {
  id: string;
  /** `meal`, `caffeine_sighting`, `alcohol_sighting`, `outdoor_block`, `screen_block`, … */
  kind: string;
  start_t: number;
  end_t: number | null;
  duration_s: number;
  dominant: { scene: string; activity: string; food_type: string };
  tick_count: number;
  open: boolean;
  /** What the wearer said about it, when they answered a question; `null` otherwise. */
  reported: Reported | null;
  /** `null` until the reasoner names the episode. */
  label: string | null;
}

/** The wearer's answer to a question about an episode (the backend's parse of it). */
export interface Reported {
  confirmed: boolean | null;
  count: number | null;
  food_type: string | null;
  note: string;
  question_id: string;
  answered_t: number | null;
}

/** One item of `GET /api/evidence/{decision_id}`: frame metadata, no bytes. */
export interface EvidenceFrame {
  decision_id: string;
  frame_ref: string;
  t: number;
  bytes: number;
}

export type DecisionAction =
  | { type: "annotate"; line: string }
  | { type: "log_insight"; category: string; text: string }
  | { type: "watch"; after_s: number; condition: string | null; reason: string }
  | { type: "speak"; text: string; urgency: string; outcome?: string }
  | { type: "ask"; text: string; answer_kind: string; fills: string; reason: string; outcome?: string }
  /** Job 4: something the phone did on the backend's behalf (calendar, Screen Time). */
  | { type: "act"; kind: string; args?: unknown; outcome?: string };

/** One item of `GET /api/decisions?limit=50` (fixture: `today_decisions.json`). */
export interface Decision {
  id: string;
  t: number;
  /** `caffeine_seen`, `food_in_frame`, `outdoor_sustained`, `watch:…`, … */
  trigger: string;
  trigger_tick_id: string;
  episode_id: string | null;
  interpretation: string;
  confidence: number;
  actions: DecisionAction[];
  spoke: boolean;
  dropped: boolean;
  drop_reason: string | null;
  latency_ms: number;
  model: string;
}
