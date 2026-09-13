"use client";
import { API_BASE } from "@/lib/api";
import { T } from "@/lib/tokens";
import type { Moment, Recap, Subscore } from "@/lib/types";
import { Chip, H2, Panel, Track } from "./Panel";

/* The body of one log entry: Overview, Summary, Moments, Suggestions.
 *
 * Everything drawn here comes back from `POST /api/recap` as it stands -- the
 * scores, the sentences and the frames are all assembled backend-side. Nothing
 * about the pipeline itself appears: tick counts, AI coverage, model names and
 * latency are facts about our software rather than about the wearer, and they
 * already have a home in the pipeline drawer. */

/** Session windows are minutes, so the duration reads as minutes, not hours. */
const duration = (s: number): string => {
  const mins = Math.round(s / 60);
  if (mins >= 60) return `${Math.floor(mins / 60)} h ${mins % 60} min`;
  return mins <= 1 ? `${Math.max(1, Math.round(s))} s` : `${mins} min`;
};

const clock = (t: number) =>
  new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** Scores arrive as raw floats (`14.933333333333334` hours of screen). Two
 *  significant-ish decimals for small values, none once it is a big count. */
const value = (v: string | number | null): string => {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (!Number.isFinite(v)) return "—";
  if (Number.isInteger(v)) return String(v);
  return Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2);
};

/** `good` earns, `flag` costs, `neutral` is a wash -- colour carries data only. */
const TONE: Record<string, "earn" | "cost" | "neutral"> = {
  good: "earn", flag: "cost", neutral: "neutral",
};
const SEVERITY_WORD: Record<string, string> = {
  good: "Worth repeating", flag: "Worth a look", neutral: "Noted",
};

function ScoreRow({ row }: { row: Subscore }) {
  const pct = Math.round((row.score ?? 0) * 100);
  const colour = pct >= 75 ? T.earn : pct >= 50 ? T.muted : T.cost;
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="w-36 shrink-0 truncate text-sm" style={{ color: T.text }} title={row.label}>
        {row.label}
      </span>
      <span className="tnum w-24 shrink-0 truncate text-sm" style={{ color: T.muted }}>
        {value(row.value)}{row.unit ? ` ${row.unit}` : ""}
      </span>
      <span className="min-w-0 flex-1"><Track value={pct} color={colour} label={row.label}/></span>
      <span className="tnum w-8 shrink-0 text-right text-sm" style={{ color: T.muted }}>{pct}</span>
    </div>
  );
}

function MomentCard({ m }: { m: Moment }) {
  return (
    <figure
      className="m-0 w-72 shrink-0 overflow-hidden"
      style={{ background: T.bg, borderRadius: T.radiusPin, border: `1px solid ${T.line}` }}
    >
      <div className="relative h-40" style={{ background: T.surface }}>
        {/* Served by the pipeline backend on a host that changes per machine, so
            next/image would need a remotePatterns entry it cannot have. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={`${API_BASE}${m.frame_url}`} alt={m.caption}
             className="h-full w-full object-cover" loading="lazy" decoding="async"/>
      </div>
      <figcaption className="flex flex-col gap-1.5 p-3">
        <div className="flex items-center justify-between gap-2">
          <Chip tone={TONE[m.severity] ?? "neutral"}>{SEVERITY_WORD[m.severity] ?? "Noted"}</Chip>
          <time className="tnum text-xs" style={{ color: T.muted }}>{clock(m.t)}</time>
        </div>
        <p className="m-0 text-sm" style={{ color: T.text }}>{m.caption}</p>
      </figcaption>
    </figure>
  );
}

export function SessionReport({ recap }: { recap: Recap }) {
  const { session, score, moments, narrative } = recap;
  // Seeded metrics are the same every session -- they are last night's wearable
  // data, not something the glasses watched. Showing them in a 2-minute report
  // pads it with rows that cannot move.
  const live = score.subscores.filter((s) => s.source === "live");
  const pct = Math.round((score.overall ?? 0) * 100);

  return (
    <div className="flex flex-col gap-6">
      <Panel labelledBy="recap-overview">
        <H2 id="recap-overview"
            sub={`${duration(session.duration_s)} · ${clock(session.from_t)}–${clock(session.to_t)}`}>
          {narrative.headline || "Session recap"}
        </H2>
        <div className="grid grid-cols-1 gap-8 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div>
            <div className="mb-3 flex items-baseline gap-2">
              <span className="tnum font-bold leading-none"
                    style={{ fontSize: 40, color: pct >= 75 ? T.earn : pct >= 50 ? T.ink : T.cost }}>
                {pct}
              </span>
              <span className="text-sm" style={{ color: T.muted }}>out of 100</span>
            </div>
            {live.length === 0
              ? <p className="m-0 text-sm" style={{ color: T.muted }}>
                  Nothing the glasses measure moved in this window.
                </p>
              : live.map((row) => <ScoreRow key={`${row.layer}:${row.metric}`} row={row}/>)}
          </div>
          <div className="flex flex-col gap-4">
            {narrative.paragraphs.length > 0 && (
              <div className="flex flex-col gap-2">
                {narrative.paragraphs.map((p, i) => (
                  <p key={i} className="m-0 text-sm leading-relaxed" style={{ color: T.text }}>{p}</p>
                ))}
              </div>
            )}
            {narrative.suggestions.length > 0 && (
              <div>
                <h3 className="m-0 mb-1.5 text-sm font-bold" style={{ color: T.ink }}>Suggestions</h3>
                <ul className="m-0 flex list-none flex-col gap-1 p-0">
                  {narrative.suggestions.map((s, i) => (
                    <li key={i} className="text-sm" style={{ color: T.text }}>
                      <span className="mr-1.5" style={{ color: T.muted }}>·</span>{s}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </Panel>

      <Panel labelledBy="recap-moments">
        <H2 id="recap-moments" sub="What the glasses saw">Moments</H2>
        {moments.length === 0
          ? <p className="m-0 text-sm" style={{ color: T.muted }}>
              Nothing crossed the line in this window. Show the camera food, a screen, or a person.
            </p>
          : <div className="strip flex gap-4 overflow-x-auto pb-2">
              {moments.map((m) => <MomentCard key={`${m.decision_id}:${m.frame_ref}`} m={m}/>)}
            </div>}
      </Panel>
    </div>
  );
}
