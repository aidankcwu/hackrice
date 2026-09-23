import type { Status } from "./types";

export interface CaptureView {
  phone: { connected?: boolean; packets?: number; lastAgeS?: number; transitMs?: number };
  loop: { ticks?: number; rateHz?: number; sensorP50Ms?: number; dropped?: number; converted?: number };
  vlm: { model?: string; coverage?: number; latencyP50Ms?: number; latencyP95Ms?: number; calls?: number; overBudget?: number; lastError?: string };
  frames: { size?: number; bytes?: number };
  /** True when the backend reports a watcher (event-driven labeling); absent on older backends. */
  watcher?: boolean;
  labeler?: LabelerView;
  watcherError?: string;
}

export interface LabelerView {
  capped?: boolean;
  lastHeartbeatAgeS?: number;
  lastWakeLatencyMs?: number;
  framesSentPerHour?: number;
  mode?: string;
  /** Heartbeat age past which the backend's health rule calls the labeler stale. */
  staleAfterS?: number;
}

export type LabelerStatus = "healthy" | "stale" | "capped";

/** Fallback stale threshold: two missed beats at the 60 s default heartbeat. */
export const DEFAULT_LABELER_STALE_S = 120;

/** Capped wins over stale: past the cost ceiling a slow heartbeat is expected. */
export function labelerStatus(labeler: LabelerView): LabelerStatus {
  if (labeler.capped) return "capped";
  const staleAfter = labeler.staleAfterS ?? DEFAULT_LABELER_STALE_S;
  if (labeler.lastHeartbeatAgeS !== undefined && labeler.lastHeartbeatAgeS > staleAfter) return "stale";
  return "healthy";
}

const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const number = (...values: unknown[]): number | undefined => {
  for (const value of values) if (typeof value === "number" && Number.isFinite(value)) return value;
  return undefined;
};
const string = (...values: unknown[]): string | undefined => {
  for (const value of values) if (typeof value === "string" && value.trim()) return value;
  return undefined;
};
const token = (line: unknown, key: string): string | undefined => {
  if (typeof line !== "string") return undefined;
  return line.match(new RegExp(`(?:^|\\s)${key}=([^\\s]+)`))?.[1];
};
const tokenNumber = (line: unknown, key: string): number | undefined => {
  const value = token(line, key)?.replace(/(?:ms|Hz|%)$/, "");
  if (value === undefined) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
};

/** Normalize both the current stats-line payload and richer future stats dictionaries. */
export function parseCapture(status: Status): CaptureView {
  const capture = object(status.capture);
  const loopRaw = capture.loop;
  const taggerRaw = capture.tagger;
  const loop = object(loopRaw);
  const tagger = object(taggerRaw);
  const phone = object(capture.phone ?? capture.link ?? capture.glasses);
  const frames = object(capture.frames ?? capture.ring);
  const connectedCount = number(phone.connected, phone.clients);
  const connectedFlag = typeof phone.connected === "boolean" ? phone.connected : connectedCount === undefined ? undefined : connectedCount > 0;
  const interval = number(status.tick_interval_s);
  const watcher = capture.watcher !== null && capture.watcher !== undefined;
  const labelerRaw = object(capture.labeler);

  return {
    phone: {
      connected: connectedFlag,
      packets: number(phone.received, phone.n_received, capture.received),
      lastAgeS: number(phone.latest_age_s, phone.last_packet_age_s, capture.latest_age_s),
      transitMs: number(phone.transit_ms, phone.latest_transit_ms, capture.transit_ms),
    },
    loop: {
      ticks: number(loop.ticks, capture.ticks, tokenNumber(loopRaw, "ticks")),
      rateHz: interval && interval > 0 ? 1 / interval : undefined,
      sensorP50Ms: number(loop.sensor_p50_ms, loop.sync_p50_ms, capture.sensor_p50_ms, tokenNumber(loopRaw, "sync_p50")),
      dropped: number(capture.dropped, loop.dropped),
      converted: number(capture.converted, loop.converted),
    },
    vlm: {
      model: string(tagger.model, capture.model, token(taggerRaw, "model")),
      coverage: number(status.ai_coverage),
      latencyP50Ms: number(tagger.latency_p50_ms, tagger.p50_ms, tokenNumber(taggerRaw, "p50")),
      latencyP95Ms: number(tagger.latency_p95_ms, tagger.p95_ms, tokenNumber(taggerRaw, "p95")),
      calls: number(tagger.calls, tokenNumber(taggerRaw, "calls")),
      overBudget: number(tagger.over_budget, tagger.overruns, tagger.overrun, tokenNumber(taggerRaw, "overrun")),
      lastError: string(tagger.last_error, capture.last_error),
    },
    frames: {
      size: number(frames.count, frames.size, capture.ring_size),
      bytes: number(frames.bytes, frames.nbytes, capture.ring_bytes),
    },
    ...(watcher ? { watcher } : {}),
    ...(Object.keys(labelerRaw).length ? {
      labeler: {
        capped: typeof labelerRaw.capped === "boolean" ? labelerRaw.capped : undefined,
        lastHeartbeatAgeS: number(labelerRaw.last_heartbeat_age_s),
        lastWakeLatencyMs: number(labelerRaw.last_wake_latency_ms),
        framesSentPerHour: number(labelerRaw.frames_sent_per_hour),
        mode: string(labelerRaw.mode),
        staleAfterS: number(capture.labeler_stale_after_s),
      },
    } : {}),
    ...(string(capture.watcher_error) ? { watcherError: string(capture.watcher_error) } : {}),
  };
}
