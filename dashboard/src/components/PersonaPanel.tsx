"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const time = (t: number) =>
  new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

/** Who T1 thinks it is working for, and what it has worked out for itself.
 *
 *  Two halves of one idea, so one panel. The textarea is the persona the
 *  operator can override (`PUT /api/persona`); underneath it are the lines the
 *  `remember` action has learned today, each removable. Both are read back into
 *  every system prompt, which is why they belong on screen rather than in a
 *  config file: during a demo, editing the top box changes the next thing the
 *  glasses say.
 *
 *  When the backend is unreachable both polls fall back to mock data, and Save
 *  is disabled for exactly as long as that lasts — writing the placeholder
 *  persona over the real one because a poll blinked is the one mistake here
 *  that would outlive the demo. */
/** Two skins for one panel: the dark chrome of the pipeline drawer, and the
 *  light card language of the Bryan page (`rounded-panel bg-surface`, ink
 *  headings, muted captions) so it sits flush with Today and Activity. */
const SKIN = {
  dark: {
    section: "panel overflow-hidden",
    head: "section-title border-b border-white/5 px-4 py-3",
    headSub: "muted",
    body: "px-4 py-3",
    textarea: "w-full resize-y rounded-md border border-white/10 bg-black/30 px-2 py-1.5 text-xs leading-relaxed text-zinc-100 placeholder:text-zinc-600 focus:border-cyan-400/40 focus:outline-none disabled:opacity-50",
    pillOn: "border-violet-400/45 bg-violet-500/20 text-violet-200",
    pillOff: "border-white/10 bg-white/[.04] text-zinc-500",
    pill: "inline-flex items-center rounded-full border px-2 text-[10px] font-black uppercase tracking-[.12em]",
    unsaved: "font-mono text-[10px] font-semibold text-amber-300",
    save: "ml-auto shrink-0 rounded-md bg-cyan-600 px-3 py-1 text-[10px] font-black uppercase tracking-[.1em] text-white transition hover:bg-cyan-500 disabled:opacity-40",
    hint: "mt-1 text-[10px] leading-relaxed text-zinc-600",
    ok: "text-emerald-300", bad: "text-rose-300", notice: "mt-1 font-mono text-[10px] font-semibold",
    subhead: "section-title border-y border-white/5 px-4 py-2",
    list: "max-h-[240px] divide-y divide-white/5 overflow-y-auto",
    row: "flex items-start gap-2 px-4 py-2",
    rowTime: "mt-[.15rem] font-mono text-[10px] text-zinc-600",
    rowText: "min-w-0 flex-1 text-[12px] leading-snug text-zinc-200",
    rowId: "mt-[.15rem] font-mono text-[10px] text-zinc-700",
    forget: "shrink-0 rounded border border-white/10 px-1.5 text-[11px] leading-5 text-zinc-500 transition hover:border-rose-400/40 hover:text-rose-300 disabled:opacity-30",
    empty: "px-4 py-5 text-xs text-zinc-600",
  },
  light: {
    section: "rounded-panel bg-surface p-6 text-text md:p-8",
    head: "flex items-baseline justify-between gap-3 text-xl font-bold leading-tight text-ink",
    headSub: "text-sm font-medium text-muted",
    body: "mt-4",
    textarea: "w-full resize-y rounded-pin border border-line bg-bg px-4 py-3 text-sm leading-relaxed text-text placeholder:text-muted focus:border-ink focus:outline-none disabled:opacity-50",
    pillOn: "bg-earn-soft text-earn",
    pillOff: "bg-surface-2 text-muted",
    pill: "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
    unsaved: "text-xs font-medium text-cost",
    save: "ml-auto shrink-0 rounded-full bg-ink px-4 py-1.5 text-sm font-medium text-bg transition hover:opacity-80 disabled:opacity-30",
    hint: "mt-2 text-sm text-muted",
    ok: "text-earn", bad: "text-cost", notice: "mt-1 text-sm font-medium",
    subhead: "mt-6 flex items-baseline justify-between gap-3 border-t border-line pt-5 text-base font-medium text-ink",
    list: "mt-2 max-h-[240px] divide-y divide-line overflow-y-auto",
    row: "flex items-start gap-3 py-2",
    rowTime: "tnum mt-[.15rem] text-xs text-muted",
    rowText: "min-w-0 flex-1 text-sm leading-snug text-text",
    rowId: "tnum mt-[.15rem] text-xs text-muted",
    forget: "shrink-0 rounded-full border border-line px-2 text-sm leading-5 text-muted transition hover:border-cost hover:text-cost disabled:opacity-30",
    empty: "py-4 text-sm text-muted",
  },
} as const;

export function PersonaPanel({ variant = "dark" }: { variant?: "dark" | "light" }) {
  const c = SKIN[variant];
  const persona = usePoll(useCallback(() => api.persona(), []), 10000);
  const profile = usePoll(useCallback(() => api.profile(50), []), 5000);

  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "bad"; text: string }>();

  const offline = persona.mock;
  const source = persona.data?.source ?? "default";
  const serverText = persona.data?.text ?? "";

  // The poll keeps running while the box is being edited, so it may only write
  // into the textarea while nobody has typed in it.
  useEffect(() => { if (!dirty) setDraft(serverText); }, [serverText, dirty]);

  const save = async () => {
    if (busy || offline) return;
    setBusy(true);
    setNotice(undefined);
    try {
      const next = await api.savePersona(draft);
      setDirty(false);
      setDraft(next.text);
      setNotice(next.source === "custom"
        ? { tone: "ok", text: "saved" }
        : { tone: "ok", text: "cleared — back to the default" });
      void persona.refresh();
    } catch (e) {
      setNotice({ tone: "bad", text: e instanceof Error ? e.message : "save failed" });
    } finally {
      setBusy(false);
    }
  };

  const forget = async (id: string) => {
    setNotice(undefined);
    try {
      await api.forgetProfileLine(id);
      void profile.refresh();
    } catch (e) {
      setNotice({ tone: "bad", text: e instanceof Error ? e.message : "remove failed" });
    }
  };

  const lines = profile.data ?? [];

  return (
    <section className={c.section}>
      <div className={c.head}>
        <span>Persona</span>
        <span className={c.headSub}>
          {offline ? "backend unreachable" : source === "custom" ? "custom" : "default"}
        </span>
      </div>

      <div className={c.body}>
        <textarea
          value={draft}
          onChange={e => { setDraft(e.target.value); setDirty(true); }}
          rows={8}
          disabled={offline}
          aria-label="The persona T1 is briefed with"
          placeholder="Who the glasses are working for…"
          className={c.textarea}
        />
        <div className="mt-2 flex items-center gap-2">
          <span className={`${c.pill} ${source === "custom" ? c.pillOn : c.pillOff}`}>
            {source}
          </span>
          {dirty && !offline && (
            <span className={c.unsaved}>unsaved</span>
          )}
          <button
            type="button"
            onClick={() => void save()}
            disabled={busy || offline || !dirty}
            className={c.save}
          >
            {busy ? "…" : "save"}
          </button>
        </div>
        <p className={c.hint}>
          {offline
            ? "Showing the placeholder persona — saving is off until the API answers."
            : "Saved empty clears the override and goes back to the built-in persona."}
        </p>
        {notice && (
          <p className={`${c.notice} ${notice.tone === "ok" ? c.ok : c.bad}`}>
            {notice.text}
          </p>
        )}
      </div>

      <div className={c.subhead}>
        <span>Learned today</span>
        <span className={c.headSub}>{lines.length ? `${lines.length} line${lines.length === 1 ? "" : "s"}` : "remember"}</span>
      </div>
      <div className={c.list}>
        {lines.map(line => (
          <div key={line.id} className={c.row}>
            <span className={c.rowTime}>{time(line.t)}</span>
            <span className={c.rowText}>{line.line}</span>
            {line.source_decision_id && (
              <span className={c.rowId}>{line.source_decision_id}</span>
            )}
            <button
              type="button"
              onClick={() => void forget(line.id)}
              disabled={offline}
              aria-label={`Forget: ${line.line}`}
              title="Forget this"
              className={c.forget}
            >
              ×
            </button>
          </div>
        ))}
        {lines.length === 0 && (
          <p className={c.empty}>Nothing learned yet today.</p>
        )}
      </div>
    </section>
  );
}
