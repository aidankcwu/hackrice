import { describe, expect, it } from "vitest";
import { DEFAULT_LABELER_STALE_S, labelerStatus, parseCapture } from "./capture";
import type { Status } from "./types";

const base: Status = {
  demo_mode: false, source: "glasses", uptime_s: 10, tick_count: 40, ai_coverage: 0.5,
  t1_busy: false, dropped_escalations: 0, last_tick_t: 0, tick_interval_s: 1.5,
};

describe("parseCapture", () => {
  it("parses a payload without the watcher keys exactly as before", () => {
    const view = parseCapture({ ...base, capture: { loop: "ticks=12 sync_p50=3.1ms", tagger: { calls: 7 } } });
    expect(view.vlm.coverage).toBe(0.5);
    expect(view.vlm.calls).toBe(7);
    expect(view.loop.ticks).toBe(12);
    expect(view).not.toHaveProperty("watcher");
    expect(view).not.toHaveProperty("labeler");
    expect(view).not.toHaveProperty("watcherError");
  });

  it("treats a null watcher as off", () => {
    const view = parseCapture({ ...base, capture: { watcher: null, labeler: null, watcher_error: null } });
    expect(view.watcher).toBeUndefined();
    expect(view.labeler).toBeUndefined();
    expect(view.watcherError).toBeUndefined();
  });

  it("parses the watcher, labeler and threshold when present", () => {
    const view = parseCapture({
      ...base,
      capture: {
        watcher: { frames: 10 },
        labeler: {
          calls_per_hour: { heartbeat: 3 }, capped: false, last_heartbeat_age_s: 42.5,
          last_wake_latency_ms: 880, frames_sent_per_hour: 9, mode: "dormant",
        },
        labeler_stale_after_s: 600,
        watcher_error: "ImportError: open_clip",
      },
    });
    expect(view.watcher).toBe(true);
    expect(view.labeler).toEqual({
      capped: false, lastHeartbeatAgeS: 42.5, lastWakeLatencyMs: 880,
      framesSentPerHour: 9, mode: "dormant", staleAfterS: 600,
    });
    expect(view.watcherError).toBe("ImportError: open_clip");
  });

  it("tolerates malformed labeler fields", () => {
    const view = parseCapture({ ...base, capture: { watcher: {}, labeler: { capped: "no", last_heartbeat_age_s: null } } });
    expect(view.watcher).toBe(true);
    expect(view.labeler?.capped).toBeUndefined();
    expect(view.labeler?.lastHeartbeatAgeS).toBeUndefined();
  });
});

describe("labelerStatus", () => {
  it("is healthy with a fresh or missing heartbeat", () => {
    expect(labelerStatus({ lastHeartbeatAgeS: 30 })).toBe("healthy");
    expect(labelerStatus({})).toBe("healthy");
    expect(labelerStatus({ lastHeartbeatAgeS: DEFAULT_LABELER_STALE_S })).toBe("healthy");
  });

  it("is stale past the default 120 s or the threshold the backend passed", () => {
    expect(labelerStatus({ lastHeartbeatAgeS: 121 })).toBe("stale");
    expect(labelerStatus({ lastHeartbeatAgeS: 300, staleAfterS: 600 })).toBe("healthy");
    expect(labelerStatus({ lastHeartbeatAgeS: 601, staleAfterS: 600 })).toBe("stale");
  });

  it("reports capped ahead of stale", () => {
    expect(labelerStatus({ capped: true, lastHeartbeatAgeS: 999 })).toBe("capped");
    expect(labelerStatus({ capped: true })).toBe("capped");
  });
});
