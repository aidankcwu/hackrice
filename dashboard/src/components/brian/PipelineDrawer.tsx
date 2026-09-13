"use client";
import { useEffect, useId, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import type { Status } from "@/lib/types";
import { StatusBar } from "@/components/StatusBar";
import { CapturePanel } from "@/components/CapturePanel";
import { TickStrip } from "@/components/TickStrip";
import { BiometricsStrip } from "@/components/BiometricsStrip";
import { DecisionFeed } from "@/components/DecisionFeed";
import { EpisodeTimeline } from "@/components/EpisodeTimeline";
import { ScoresPanel } from "@/components/ScoresPanel";
import { HealthspanPanel } from "@/components/HealthspanPanel";
import { MemoryPanel, SevenDayPanel } from "@/components/MemoryPanels";

/**
 * The old dark dashboard, folded into a collapsible drawer under the Brian page.
 * Collapsed by default; the heavy polling (ticks at 1 Hz, decisions at 2 Hz)
 * only runs while the drawer is open because the panels are only mounted then.
 */

const STORAGE_KEY = "brian.pipeline.open";

// localStorage can throw (private mode, blocked storage); the drawer must
// still work, so both accessors swallow and fall back to "closed".
function readStoredOpen(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeStoredOpen(open: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, open ? "1" : "0");
  } catch {
    /* nothing to persist to */
  }
}

/** Status plus the wall-clock second it was fetched, so "n s ago" is computed once per poll, not per render. */
type StampedStatus = Status & { fetched_t: number };

async function stampedStatus() {
  const r = await api.status();
  return { ...r, data: { ...r.data, fetched_t: Date.now() / 1000 } as StampedStatus };
}

function statusLine(status: StampedStatus | undefined, offline: boolean): string {
  if (!status) return "connecting…";
  // A poll that threw leaves the last good status on screen; say "offline"
  // rather than print a stale tick count as if it were current.
  if (offline) return "offline — backend unreachable";
  const age = Math.max(0, Math.round(status.fetched_t - status.last_tick_t));
  return `${status.source} · ${status.tick_count.toLocaleString("en-US")} ticks · AI ${Math.round(status.ai_coverage * 100)}% · last tick ${age}s ago`;
}

/** The previous `app/page.tsx` composition, verbatim, minus its outer padding (the drawer supplies it). */
function PipelinePanels() {
  const status = usePoll(api.status, 1000),
    ticks = usePoll(() => api.ticks(30), 1000),
    decisions = usePoll(() => api.decisions(50), 500);
  const episodes = usePoll(() => api.episodes(), 5000),
    scores = usePoll(() => api.scores("daily"), 5000),
    seeded = usePoll(() => api.seeded(7), 5000),
    healthspan = usePoll(() => api.healthspan(), 5000);
  const summary = usePoll(api.summaryToday, 5000),
    pending = usePoll(api.pendingChecks, 5000);
  // A read that fails now throws instead of serving a fixture (product rule R1),
  // so "offline" is `error`, and `mock` only means NEXT_PUBLIC_MOCK=1 was set.
  const polls = [status, ticks, decisions, episodes, scores, seeded, healthspan, summary, pending];
  const offline = polls.some((x) => x.error);
  const isMock = polls.some((x) => x.mock);
  return (
    <div className="mx-auto max-w-[1800px]">
      {(offline || isMock) && (
        <div className="mb-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs font-semibold text-amber-200">
          {isMock ? "NEXT_PUBLIC_MOCK=1 — these panels are sample data, not your pipeline" : "API offline — panels hold the last answer the backend gave"}
        </div>
      )}
      <StatusBar status={status.data} />
      <CapturePanel status={status.data} />
      <div className="mt-3">
        <TickStrip ticks={ticks.data} />
      </div>
      <div className="mt-3">
        <BiometricsStrip />
      </div>
      <div className="mt-3 grid items-start gap-3 xl:grid-cols-[minmax(0,1.65fr)_minmax(300px,.75fr)_minmax(320px,.85fr)]">
        <div className="space-y-3">
          <DecisionFeed decisions={decisions.data} />
          <EpisodeTimeline episodes={episodes.data} />
        </div>
        <div className="space-y-3">
          <MemoryPanel summary={summary.data} pending={pending.data} />
          <SevenDayPanel rows={seeded.data} />
        </div>
        <div className="space-y-3">
          <HealthspanPanel data={healthspan.data} />
          <ScoresPanel scores={scores.data} />
        </div>
      </div>
      <footer className="py-4 text-center text-[10px] uppercase tracking-[.2em] text-zinc-700">
        Always writes · rarely speaks · never queues
      </footer>
    </div>
  );
}

export function PipelineDrawer() {
  // Server and first client render agree on "closed"; the stored preference is
  // applied after mount so hydration never mismatches.
  const [open, setOpen] = useState(false);
  useEffect(() => {
    setOpen(readStoredOpen());
  }, []);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    writeStoredOpen(next);
  };

  const status = usePoll(stampedStatus, 2000);
  const panelId = useId();
  const labelId = useId();
  const statusId = useId();
  const Chevron = open ? ChevronUp : ChevronDown;

  return (
    <section className="mx-auto w-full max-w-6xl px-6 py-6" aria-labelledby={labelId}>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-controls={panelId}
        aria-labelledby={labelId}
        aria-describedby={statusId}
        className="tile flex min-h-11 w-full flex-wrap items-center gap-x-4 gap-y-1 rounded-panel bg-surface px-5 py-2 text-left text-ink"
      >
        <span id={labelId} className="flex items-center gap-2 text-base font-medium">
          Pipeline
          <Chevron aria-hidden="true" size={18} strokeWidth={2} />
        </span>
        <span id={statusId} className="tnum ml-auto text-sm text-muted">
          {statusLine(status.data, status.error !== undefined)}
        </span>
      </button>
      {open && (
        <div id={panelId} className="lifeos-dark mt-3 rounded-panel p-3 lg:p-5">
          <PipelinePanels />
        </div>
      )}
    </section>
  );
}

export default PipelineDrawer;
