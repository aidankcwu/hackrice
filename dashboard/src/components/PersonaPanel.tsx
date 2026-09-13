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
export function PersonaPanel() {
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
    <section className="panel overflow-hidden">
      <div className="section-title border-b border-white/5 px-4 py-3">
        <span>Persona</span>
        <span className="muted">
          {offline ? "backend unreachable" : source === "custom" ? "custom" : "default"}
        </span>
      </div>

      <div className="px-4 py-3">
        <textarea
          value={draft}
          onChange={e => { setDraft(e.target.value); setDirty(true); }}
          rows={8}
          disabled={offline}
          aria-label="The persona T1 is briefed with"
          placeholder="Who the glasses are working for…"
          className="w-full resize-y rounded-md border border-white/10 bg-black/30 px-2 py-1.5 text-xs leading-relaxed text-zinc-100 placeholder:text-zinc-600 focus:border-cyan-400/40 focus:outline-none disabled:opacity-50"
        />
        <div className="mt-2 flex items-center gap-2">
          <span className={`inline-flex items-center rounded-full border px-2 text-[10px] font-black uppercase tracking-[.12em] ${
            source === "custom"
              ? "border-violet-400/45 bg-violet-500/20 text-violet-200"
              : "border-white/10 bg-white/[.04] text-zinc-500"
          }`}>
            {source}
          </span>
          {dirty && !offline && (
            <span className="font-mono text-[10px] font-semibold text-amber-300">unsaved</span>
          )}
          <button
            type="button"
            onClick={() => void save()}
            disabled={busy || offline || !dirty}
            className="ml-auto shrink-0 rounded-md bg-cyan-600 px-3 py-1 text-[10px] font-black uppercase tracking-[.1em] text-white transition hover:bg-cyan-500 disabled:opacity-40"
          >
            {busy ? "…" : "save"}
          </button>
        </div>
        <p className="mt-1 text-[10px] leading-relaxed text-zinc-600">
          {offline
            ? "Showing the placeholder persona — saving is off until the API answers."
            : "Saved empty clears the override and goes back to the built-in persona."}
        </p>
        {notice && (
          <p className={`mt-1 font-mono text-[10px] font-semibold ${notice.tone === "ok" ? "text-emerald-300" : "text-rose-300"}`}>
            {notice.text}
          </p>
        )}
      </div>

      <div className="section-title border-y border-white/5 px-4 py-2">
        <span>Learned today</span>
        <span className="muted">{lines.length ? `${lines.length} line${lines.length === 1 ? "" : "s"}` : "remember"}</span>
      </div>
      <div className="max-h-[240px] divide-y divide-white/5 overflow-y-auto">
        {lines.map(line => (
          <div key={line.id} className="flex items-start gap-2 px-4 py-2">
            <span className="mt-[.15rem] font-mono text-[10px] text-zinc-600">{time(line.t)}</span>
            <span className="min-w-0 flex-1 text-[12px] leading-snug text-zinc-200">{line.line}</span>
            {line.source_decision_id && (
              <span className="mt-[.15rem] font-mono text-[10px] text-zinc-700">{line.source_decision_id}</span>
            )}
            <button
              type="button"
              onClick={() => void forget(line.id)}
              disabled={offline}
              aria-label={`Forget: ${line.line}`}
              title="Forget this"
              className="shrink-0 rounded border border-white/10 px-1.5 text-[11px] leading-5 text-zinc-500 transition hover:border-rose-400/40 hover:text-rose-300 disabled:opacity-30"
            >
              ×
            </button>
          </div>
        ))}
        {lines.length === 0 && (
          <p className="px-4 py-5 text-xs text-zinc-600">Nothing learned yet today.</p>
        )}
      </div>
    </section>
  );
}
