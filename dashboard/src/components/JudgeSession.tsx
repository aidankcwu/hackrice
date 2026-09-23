"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useBackendUrl } from "@/lib/useBackendUrl";
import type { Moment, Recap, Session, Subscore } from "@/lib/types";

/** Ending a session auto-generates its recap.
 *
 *  `POST /api/session/end` only closes the window -- it does not build anything.
 *  The chaining lives here rather than in that endpoint because the endpoint is
 *  Person B's and has tests pinning its current behaviour. Two calls cost nothing.
 */

const clock = (t: number) => new Date(t * 1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false});
const mmss = (s: number) => `${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,"0")}`;
// `frame_url` is server-relative; the dashboard may be on a different origin,
// and an <img> cannot send the token header, so it rides as `?token=`
// (useBackendUrl → lib/runtime.ts backendUrl).

const SEVERITY: Record<string,string> = {
  good: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  bad: "border-rose-500/40 bg-rose-500/10 text-rose-300",
  neutral: "border-zinc-600/40 bg-zinc-600/10 text-zinc-400",
};

function ScoreDial({value}:{value:number}) {
  const pct = Math.round((value ?? 0) * 100);
  const tone = pct >= 75 ? "text-emerald-400" : pct >= 50 ? "text-amber-400" : "text-rose-400";
  return <div className="flex flex-col items-center justify-center">
    <strong className={`text-4xl font-black tabular-nums ${tone}`}>{pct}</strong>
    <span className="text-[9px] font-bold uppercase tracking-[.16em] text-zinc-600">longevity score</span>
  </div>;
}

function SubscoreRow({row}:{row:Subscore}) {
  const pct = Math.round((row.score ?? 0) * 100);
  const bar = pct >= 75 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-rose-500";
  return <div className="flex items-center gap-2 py-1">
    <span className="w-32 shrink-0 truncate text-[11px] font-semibold text-zinc-300" title={row.label}>{row.label}</span>
    <span className="w-20 shrink-0 truncate text-[10px] tabular-nums text-zinc-500">{row.value ?? "—"}{row.unit ? ` ${row.unit}` : ""}</span>
    <span className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-zinc-800"><span className={`block h-full ${bar}`} style={{width:`${pct}%`}}/></span>
    <span className="w-8 shrink-0 text-right text-[10px] font-bold tabular-nums text-zinc-400">{pct}</span>
    {row.source === "seeded" && <span className="badge shrink-0">seed</span>}
  </div>;
}

function MomentCard({moment}:{moment:Moment}) {
  const tone = SEVERITY[moment.severity] ?? SEVERITY.neutral;
  const src = useBackendUrl(moment.frame_url);
  return <figure className={`w-40 shrink-0 overflow-hidden rounded-md border ${tone}`}>
    {/* eslint-disable-next-line @next/next/no-img-element -- evidence JPEG off the pipeline, not a static asset */}
    <img src={src} alt={moment.caption} className="h-24 w-full bg-zinc-900 object-cover" loading="lazy"/>
    <figcaption className="space-y-1 p-2">
      <div className="flex items-center justify-between gap-1">
        <span className="text-[9px] font-black uppercase tracking-[.1em]">{moment.category}</span>
        <span className="text-[9px] tabular-nums text-zinc-500">{clock(moment.t)}</span>
      </div>
      <p className="text-[10px] leading-snug text-zinc-300">{moment.caption}</p>
      {moment.blurry && <span className="badge">blurry</span>}
    </figcaption>
  </figure>;
}

export function JudgeSession() {
  const [session, setSession] = useState<Session|null>(null);
  const [recap, setRecap] = useState<Recap|null>(null);
  const [busy, setBusy] = useState<"" | "starting" | "ending" | "generating">("");
  const [error, setError] = useState<string>("");
  const [now, setNow] = useState(Date.now()/1000);

  // Reflect a session started from elsewhere (curl, a second tab, a page reload),
  // and surface the last recap so refreshing the page does not appear to lose it.
  useEffect(() => {
    let live = true;
    const sync = async () => {
      const s = await api.sessionCurrent();
      if (!live) return;
      setSession(s);
      // A recap whose window closed before this session opened belongs to the
      // previous one. Without this, a reload -- or a session started from
      // anywhere but this button -- leaves the last judge's recap on screen
      // while the next one records.
      //
      // Compared on `to_t`, not `generated_at`: both `to_t` and `started_t` are
      // the pipeline's tick clock, which under `--speed N` is not wall time,
      // and `generated_at` is. Mixing the two misjudges staleness in replay.
      if (s && s.ended_t === null) {
        setRecap(r => (r && (r.session.id === s.id || r.session.to_t > s.started_t) ? r : null));
      }
    };
    void sync();
    void api.latestRecap().then(r => { if (live && r) setRecap(r); });
    const id = setInterval(sync, 3000);
    return () => { live = false; clearInterval(id); };
  }, []);

  // A local ticker for the elapsed readout; the session clock itself is the backend's.
  useEffect(() => {
    if (!session || session.ended_t !== null) return;
    const id = setInterval(() => setNow(Date.now()/1000), 1000);
    return () => clearInterval(id);
  }, [session]);

  const start = useCallback(async () => {
    setError(""); setBusy("starting");
    try {
      setRecap(null);                       // the old recap is not this session's
      setSession(await api.sessionStart(`Judge session ${clock(Date.now()/1000)}`));
    } catch (e) { setError(e instanceof Error ? e.message : "could not start"); }
    finally { setBusy(""); }
  }, []);

  const end = useCallback(async () => {
    setError(""); setBusy("ending");
    try {
      const ended = await api.sessionEnd();
      setSession(null);
      // The server generates the recap the moment a session ends; wait for it
      // to land rather than asking for a second one (which would also speak twice).
      setBusy("generating");
      let landed: Recap|null = null;
      for (let i = 0; i < 45 && !landed; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const r = await api.latestRecap();
        if (r && r.session.id === ended.id) landed = r;
      }
      setRecap(landed ?? await api.recap(ended.id, false));
    } catch (e) { setError(e instanceof Error ? e.message : "could not end session"); }
    finally { setBusy(""); }
  }, []);

  const open = session !== null && session.ended_t === null;
  const elapsed = open ? Math.max(0, now - session.started_t) : 0;
  const working = busy !== "";

  return <section className="panel mt-3 px-4 py-3">
    <div className="section-title">
      <h2>Judge session</h2>
      <span className="muted">{open ? "recording — recap generates when you end it" : "start a session, then end it for a recap"}</span>
    </div>

    <div className="flex flex-wrap items-center gap-3">
      {open
        ? <button onClick={end} disabled={working} className="rounded-md bg-rose-600 px-4 py-2 text-xs font-black uppercase tracking-[.1em] text-white hover:bg-rose-500 disabled:opacity-50">
            {busy === "ending" ? "Ending…" : busy === "generating" ? "Generating recap…" : "End session + recap"}
          </button>
        : <button onClick={start} disabled={working} className="rounded-md bg-emerald-600 px-4 py-2 text-xs font-black uppercase tracking-[.1em] text-white hover:bg-emerald-500 disabled:opacity-50">
            {busy === "starting" ? "Starting…" : "Start judge session"}
          </button>}

      {open && <span className="flex items-center gap-2 text-xs font-semibold text-zinc-300">
        <span className="h-2 w-2 animate-pulse rounded-full bg-rose-500"/>
        <span className="tabular-nums">{mmss(elapsed)}</span>
        <span className="muted">since {clock(session.started_t)}</span>
      </span>}

      {busy === "generating" && <span className="muted">scoring the window, picking moments, writing the narrative…</span>}
      {error && <span className="text-xs font-semibold text-rose-400">{error}</span>}
    </div>

    {recap && <Report recap={recap}/>}
  </section>;
}

function Report({recap}:{recap:Recap}) {
  const {session, score, moments, narrative} = recap;
  return <div className="mt-4 border-t border-white/8 pt-4">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0 flex-1">
        <p className="eyebrow">recap</p>
        <h3 className="mt-1 text-lg font-black leading-tight text-zinc-100">{narrative.headline || "Session recap"}</h3>
        <p className="muted mt-1">
          {mmss(session.duration_s)} · {session.tick_count} ticks · {Math.round((session.ai_coverage ?? 0)*100)}% AI coverage
          · {session.decision_count} decision{session.decision_count === 1 ? "" : "s"}
          {recap.spoken ? " · spoken aloud" : " · not spoken"}
        </p>
      </div>
      <ScoreDial value={score.overall}/>
    </div>

    {narrative.paragraphs.length > 0 && <div className="mt-3 space-y-1.5">
      {narrative.paragraphs.map((p,i) => <p key={i} className="text-xs leading-relaxed text-zinc-300">{p}</p>)}
    </div>}

    <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <div>
        <h4 className="mb-1 text-[10px] font-extrabold uppercase tracking-[.14em] text-zinc-400">Subscores</h4>
        {score.subscores.length === 0
          ? <p className="muted">nothing scorable in this window</p>
          : score.subscores.map(row => <SubscoreRow key={`${row.layer}:${row.metric}`} row={row}/>)}
      </div>
      <div className="min-w-0">
        <h4 className="mb-1 text-[10px] font-extrabold uppercase tracking-[.14em] text-zinc-400">Key moments</h4>
        {moments.length === 0
          ? <p className="muted">no moment saved a frame in this window — nothing tripped the trigger gate</p>
          : <div className="flex gap-2 overflow-x-auto pb-1">{moments.map(m => <MomentCard key={`${m.decision_id}:${m.frame_ref}`} moment={m}/>)}</div>}
      </div>
    </div>

    {narrative.suggestions.length > 0 && <div className="mt-3">
      <h4 className="mb-1 text-[10px] font-extrabold uppercase tracking-[.14em] text-zinc-400">Suggestions</h4>
      <ul className="space-y-1">{narrative.suggestions.map((s,i) =>
        <li key={i} className="text-xs text-zinc-300"><span className="mr-1.5 text-emerald-400">→</span>{s}</li>)}</ul>
    </div>}
  </div>;
}
