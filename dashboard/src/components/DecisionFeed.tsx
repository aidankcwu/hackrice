"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Decision, DecisionAction } from "@/lib/types";

const time = (t: number) =>
  new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const hhmm = (t: number) =>
  new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const short = (s: string) => (s.length > 42 ? `${s.slice(0, 39)}…` : s);

/** Stable colour per trigger family — the same trigger always reads the same. */
const SLATE = "border-slate-400/25 bg-slate-400/10 text-slate-200";
const FAMILIES: Array<[string, string]> = [
  ["screen", "border-blue-400/30 bg-blue-500/15 text-blue-200"],
  ["food", "border-orange-400/30 bg-orange-500/15 text-orange-200"],
  ["caffeine", "border-amber-400/30 bg-amber-500/15 text-amber-200"],
  ["alcohol", "border-purple-400/30 bg-purple-500/15 text-purple-200"],
  ["people", "border-emerald-400/30 bg-emerald-500/15 text-emerald-200"],
  ["outdoor", "border-teal-400/30 bg-teal-500/15 text-teal-200"],
  ["biometric", "border-rose-400/30 bg-rose-500/15 text-rose-200"],
  ["stillness", "border-zinc-400/25 bg-zinc-400/10 text-zinc-300"],
];
function triggerTone(trigger: string): string {
  const t = trigger.toLowerCase();
  if (t.startsWith("watch:")) return "border-indigo-400/30 bg-indigo-500/15 text-indigo-200";
  for (const [key, tone] of FAMILIES) if (t.includes(key)) return tone;
  return SLATE;
}

const URGENCY: Record<string, string> = {
  low: "border-zinc-400/25 text-zinc-300",
  normal: "border-amber-400/35 text-amber-200",
  high: "border-rose-400/40 text-rose-200",
};

const ICON: Record<DecisionAction["type"], string> = {
  annotate: "✎",
  log_insight: "◆",
  watch: "◷",
  speak: "◖",
  nothing: "·",
};

function TriggerBadge({ trigger }: { trigger: string }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-[.1rem] font-mono text-[11px] font-semibold ${triggerTone(trigger)}`}>
      {trigger}
    </span>
  );
}

function ActionRow({ action, spoke }: { action: DecisionAction; spoke: boolean }) {
  return (
    <div className="flex items-start gap-2 py-[.15rem] text-xs leading-relaxed">
      <span className="w-4 shrink-0 text-center text-zinc-500">{ICON[action.type]}</span>
      <span className="w-[4.5rem] shrink-0 text-[10px] font-bold uppercase tracking-[.1em] text-zinc-500">
        {action.type === "log_insight" ? "insight" : action.type}
      </span>
      <span className="min-w-0 flex-1">
        {action.type === "annotate" && <span className="text-zinc-300">{action.line}</span>}
        {action.type === "log_insight" && (
          <span className="text-zinc-300">
            <span className="mr-1 font-mono text-[11px] text-emerald-300">[{action.category}]</span>
            {action.text}
          </span>
        )}
        {action.type === "watch" && (
          <span className="text-zinc-300">
            <span className="font-mono text-[11px] text-indigo-300">in {action.after_s}s</span>
            {action.reason ? <span className="text-zinc-400"> · {action.reason}</span> : null}
          </span>
        )}
        {action.type === "speak" && (
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className={`rounded px-1.5 py-[.1rem] ${spoke ? "bg-cyan-400/15 font-semibold text-cyan-100" : "bg-white/[.06] text-zinc-300"}`}>
              “{action.text}”
            </span>
            <span className={`rounded-full border px-1.5 text-[10px] font-bold uppercase tracking-[.08em] ${URGENCY[action.urgency] ?? URGENCY.low}`}>
              {action.urgency}
            </span>
            {!spoke && (
              <span className="text-[10px] font-semibold uppercase tracking-[.08em] text-amber-300/80">
                proposed, suppressed by limiter
              </span>
            )}
          </span>
        )}
        {action.type === "nothing" && <span className="text-zinc-500">no action</span>}
      </span>
    </div>
  );
}

function DecisionCard({ d, flash }: { d: Decision; flash: boolean }) {
  const silent = !d.spoke && !d.dropped;
  return (
    <article
      className={`px-4 py-3 ${flash ? "decision-flash" : ""} ${d.dropped ? "bg-rose-950/25" : d.spoke ? "bg-cyan-950/25" : ""}`}
    >
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-mono text-[11px] text-zinc-500">{time(d.t)}</span>
        <TriggerBadge trigger={d.trigger} />
        {d.dropped ? (
          <span className="rounded-full border border-rose-400/45 bg-rose-500/20 px-2 text-[10px] font-black uppercase tracking-[.1em] text-rose-200">
            dropped{d.drop_reason ? ` · ${d.drop_reason}` : ""}
          </span>
        ) : d.spoke ? (
          <span className="rounded-full border border-cyan-300/50 bg-cyan-400/25 px-2 text-[10px] font-black uppercase tracking-[.12em] text-cyan-100">
            spoke
          </span>
        ) : (
          <span className="rounded-full border border-white/10 px-2 text-[10px] font-bold uppercase tracking-[.12em] text-zinc-500">
            silent
          </span>
        )}
        <span className="ml-auto flex items-center gap-2 font-mono text-[10px] text-zinc-600">
          <span>{d.model}</span>
          <span>{d.latency_ms} ms</span>
        </span>
      </header>

      <p className={`mt-2 text-[13px] leading-snug ${silent ? "text-zinc-300" : "text-zinc-100"}`}>
        {d.interpretation}
      </p>

      <div className="mt-1.5 flex items-center gap-2">
        <span className="block h-1 w-24 overflow-hidden rounded-full bg-white/10">
          <span
            className="block h-full rounded-full bg-zinc-400"
            style={{ width: `${Math.round(Math.min(1, Math.max(0, d.confidence)) * 100)}%` }}
          />
        </span>
        <span className="font-mono text-[10px] text-zinc-500">conf {d.confidence.toFixed(2)}</span>
      </div>

      <div className="mt-2 border-t border-white/5 pt-1.5">
        {d.actions.length === 0 ? (
          <div className="flex items-start gap-2 py-[.15rem] text-xs">
            <span className="w-4 shrink-0 text-center text-zinc-600">·</span>
            <span className="text-zinc-500">no action</span>
          </div>
        ) : (
          d.actions.map((a, i) => <ActionRow key={`${a.type}-${i}`} action={a} spoke={d.spoke} />)
        )}
      </div>
    </article>
  );
}

function CompactRow({ d, flash }: { d: Decision; flash: boolean }) {
  const silent = !d.spoke && !d.dropped;
  return (
    <div
      className={`flex items-start gap-2 px-4 py-2 font-mono text-[12px] ${flash ? "decision-flash" : ""} ${
        d.dropped ? "bg-rose-950/25 text-rose-300" : d.spoke ? "bg-cyan-950/25" : "text-zinc-400"
      }`}
    >
      <span className="w-4 text-base leading-4">{d.spoke ? "◖" : d.dropped ? "!" : "·"}</span>
      <span className="text-zinc-500">{hhmm(d.t)}</span>
      <span className={silent ? "text-zinc-400" : "font-semibold text-current"}>
        · {d.trigger} · {short(d.interpretation.toLowerCase())} ·{" "}
        {d.actions.map(a => a.type).join(", ") || "no action"} ·{" "}
        {d.dropped ? "dropped" : d.spoke ? "spoken" : "silent"}
      </span>
    </div>
  );
}

const STORAGE_KEY = "decisionFeed.mode";

export function DecisionFeed({ decisions = [] }: { decisions?: Decision[] }) {
  const [compact, setCompact] = useState(false);
  const rows = useMemo(() => [...decisions].sort((a, b) => b.t - a.t), [decisions]);
  const seen = useRef<Set<string> | null>(null);
  const [flashId, setFlashId] = useState<string>();

  // Default is detailed; the choice survives a reload. Read after mount so the
  // server render and the first client render agree.
  useEffect(() => {
    try {
      if (localStorage.getItem(STORAGE_KEY) === "compact") setCompact(true);
    } catch {
      /* storage unavailable — keep the default */
    }
  }, []);

  const toggle = () =>
    setCompact(prev => {
      const next = !prev;
      try {
        localStorage.setItem(STORAGE_KEY, next ? "compact" : "detailed");
      } catch {
        /* storage unavailable — the toggle still works for this session */
      }
      return next;
    });

  // Flash the newest card once, the first time it arrives. The class stays put
  // afterwards; the animation has already run, so it does not loop.
  useEffect(() => {
    const newest = rows[0];
    if (!newest) return;
    const first = seen.current === null;
    const ids = first ? new Set<string>() : seen.current!;
    if (first) seen.current = ids;
    const fresh = !ids.has(newest.id);
    for (const d of rows) ids.add(d.id);
    if (fresh && !first) setFlashId(newest.id);
  }, [rows]);

  return (
    <section className="panel min-h-[480px] overflow-hidden">
      <div className="section-title border-b border-white/5 px-4 py-3">
        <span>Decision feed</span>
        <span className="flex items-center gap-3">
          <span className="muted">every escalation · newest first</span>
          <button
            type="button"
            onClick={toggle}
            aria-pressed={compact}
            title="Toggle between the one-line feed and full decision cards"
            className="rounded-full border border-white/10 bg-white/[.04] px-2 py-[.15rem] text-[10px] font-bold uppercase tracking-[.1em] text-zinc-400 transition hover:border-white/20 hover:text-zinc-200"
          >
            {compact ? "compact" : "detailed"}
          </button>
        </span>
      </div>
      <div className="divide-y divide-white/5">
        {rows.map(d =>
          compact ? (
            <CompactRow key={d.id} d={d} flash={d.id === flashId} />
          ) : (
            <DecisionCard key={d.id} d={d} flash={d.id === flashId} />
          ),
        )}
        {rows.length === 0 && <p className="px-4 py-6 text-xs text-zinc-600">No escalations yet.</p>}
      </div>
    </section>
  );
}
