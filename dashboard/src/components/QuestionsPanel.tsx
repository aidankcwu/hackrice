"use client";
import { useCallback, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import type { ParsedAnswer, Question, QuestionStatus } from "@/lib/types";

const time = (t: number) =>
  new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

/** Same badge vocabulary as the decision feed: a bordered pill, black uppercase
 *  micro-caps. Open pulses because it is the only state the wearer can still change. */
const STATUS: Record<QuestionStatus, { label: string; tone: string; pulse?: boolean }> = {
  open: { label: "open", tone: "border-cyan-300/50 bg-cyan-400/25 text-cyan-100", pulse: true },
  answered: { label: "answered", tone: "border-emerald-400/45 bg-emerald-500/20 text-emerald-200" },
  expired: { label: "expired", tone: "border-white/10 bg-white/[.04] text-zinc-500" },
  suppressed: { label: "suppressed", tone: "border-rose-400/45 bg-rose-500/20 text-rose-200" },
};

const KIND_TONE: Record<string, string> = {
  yes_no: "border-blue-400/30 bg-blue-500/15 text-blue-200",
  count: "border-amber-400/30 bg-amber-500/15 text-amber-200",
  free: "border-slate-400/25 bg-slate-400/10 text-slate-200",
};

function StatusBadge({ q }: { q: Question }) {
  const s = STATUS[q.status] ?? STATUS.expired;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 text-[10px] font-black uppercase tracking-[.12em] ${s.tone} ${s.pulse ? "animate-pulse" : ""}`}
    >
      {s.label}
      {q.status === "suppressed" && q.suppressed_reason ? ` · ${q.suppressed_reason}` : ""}
    </span>
  );
}

/** `parsed` is `{}` for a question nobody answered and partially filled otherwise,
 *  so print only the keys the answer actually settled. */
function ParsedChips({ parsed }: { parsed: ParsedAnswer }) {
  const chips: Array<[string, string]> = [];
  if (parsed.confirmed !== null && parsed.confirmed !== undefined) chips.push(["confirmed", parsed.confirmed ? "yes" : "no"]);
  if (parsed.count !== null && parsed.count !== undefined) chips.push(["count", String(parsed.count)]);
  if (parsed.food_type) chips.push(["food", parsed.food_type]);
  if (parsed.note) chips.push(["note", parsed.note]);
  if (parsed.followup) chips.push(["followup", parsed.followup]);
  if (parsed.understood === false) chips.push(["understood", "no"]);
  if (chips.length === 0) return <span className="text-[11px] text-zinc-600">nothing parsed</span>;
  return (
    <span className="flex flex-wrap items-center gap-1">
      {chips.map(([k, v]) => (
        <span key={k} className="inline-flex items-baseline gap-1 rounded border border-white/10 bg-white/[.04] px-1.5 py-[.05rem] text-[11px]">
          <span className="font-mono text-[9px] font-bold uppercase tracking-[.08em] text-zinc-500">{k}</span>
          <span className="text-zinc-200">{v}</span>
        </span>
      ))}
    </span>
  );
}

function AnswerForm({ q, onDone }: { q: Question; onDone: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async () => {
    const body = text.trim();
    if (!body || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.answer(q.id, body);
      setText("");
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "answer failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-2 border-t border-white/5 pt-2">
      <div className="flex items-center gap-2">
        <input
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") void submit(); }}
          placeholder={q.answer_kind === "count" ? "e.g. two" : q.answer_kind === "yes_no" ? "yes / no" : "what you would have said"}
          aria-label="Answer this question"
          className="min-w-0 flex-1 rounded-md border border-white/10 bg-black/30 px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-cyan-400/40 focus:outline-none"
        />
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy || !text.trim()}
          className="shrink-0 rounded-md bg-cyan-600 px-3 py-1 text-[10px] font-black uppercase tracking-[.1em] text-white transition hover:bg-cyan-500 disabled:opacity-40"
        >
          {busy ? "…" : "answer"}
        </button>
      </div>
      {error && <p className="mt-1 text-[10px] font-semibold text-rose-300">{error}</p>}
    </div>
  );
}

function QuestionCard({ q, onAnswered }: { q: Question; onAnswered: () => void }) {
  const answered = q.status === "answered";
  return (
    <article className={`px-4 py-3 ${q.status === "open" ? "bg-cyan-950/25" : q.status === "suppressed" ? "bg-rose-950/20" : ""}`}>
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-mono text-[11px] text-zinc-500">{time(q.created_t)}</span>
        <StatusBadge q={q} />
        <span className={`inline-flex items-center rounded-full border px-2 py-[.1rem] font-mono text-[11px] font-semibold ${KIND_TONE[q.answer_kind] ?? KIND_TONE.free}`}>
          {q.answer_kind}
        </span>
        <span className="ml-auto flex items-center gap-2 font-mono text-[10px] text-zinc-600">
          {q.decision_id && <span>{q.decision_id}</span>}
          {q.episode_id && <span>{q.episode_id}</span>}
          <span>fills {q.fills}</span>
        </span>
      </header>

      <p className={`mt-2 text-[13px] leading-snug ${answered || q.status === "open" ? "text-zinc-100" : "text-zinc-400"}`}>
        “{q.question}”
      </p>
      {q.followup_of && (
        <p className="mt-1 font-mono text-[10px] text-zinc-600">follow-up of {q.followup_of}</p>
      )}

      {answered && (
        <div className="mt-2 space-y-1.5 border-t border-white/5 pt-2">
          <div className="flex items-start gap-2 text-xs leading-relaxed">
            <span className="w-[4.5rem] shrink-0 text-[10px] font-bold uppercase tracking-[.1em] text-zinc-500">said</span>
            <span className="min-w-0 flex-1">
              <span className="rounded bg-emerald-400/15 px-1.5 py-[.1rem] font-semibold text-emerald-100">
                “{q.answer_text ?? ""}”
              </span>
              <span className={`ml-2 rounded-full border px-1.5 text-[10px] font-bold uppercase tracking-[.08em] ${q.heard ? "border-emerald-400/35 text-emerald-200" : "border-amber-400/35 text-amber-200"}`}>
                {q.heard ? "heard" : "not heard"}
              </span>
              {q.answer_t !== null && <span className="ml-2 font-mono text-[10px] text-zinc-600">{time(q.answer_t)}</span>}
            </span>
          </div>
          <div className="flex items-start gap-2 text-xs leading-relaxed">
            <span className="w-[4.5rem] shrink-0 text-[10px] font-bold uppercase tracking-[.1em] text-zinc-500">parsed</span>
            <span className="min-w-0 flex-1"><ParsedChips parsed={q.parsed ?? {}} /></span>
          </div>
        </div>
      )}

      {q.status === "open" && <AnswerForm q={q} onDone={onAnswered} />}
    </article>
  );
}

function AskBar({ onAsked }: { onAsked: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "warn" | "bad"; text: string }>();
  const submit = async () => {
    const body = text.trim();
    if (!body || busy) return;
    setBusy(true);
    setNotice(undefined);
    try {
      const result = await api.ask(body);
      // The ask goes through the same §4 guards the reasoner does, so a 200 with
      // a null question_id is the normal "a guard said no" answer, not an error.
      setNotice(result.suppressed_reason
        ? { tone: "warn", text: `suppressed · ${result.suppressed_reason}` }
        : { tone: "ok", text: `sent · ${result.question_id ?? ""}` });
      setText("");
      onAsked();
    } catch (e) {
      setNotice({ tone: "bad", text: e instanceof Error ? e.message : "ask failed" });
    } finally {
      setBusy(false);
    }
  };
  const tone = notice?.tone === "ok" ? "text-emerald-300" : notice?.tone === "warn" ? "text-amber-300" : "text-rose-300";
  return (
    <div className="border-b border-white/5 px-4 py-2">
      <div className="flex items-center gap-2">
        <input
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") void submit(); }}
          placeholder="Ask the wearer something…"
          aria-label="Ask the wearer a question"
          className="min-w-0 flex-1 rounded-md border border-white/10 bg-black/30 px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-cyan-400/40 focus:outline-none"
        />
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy || !text.trim()}
          className="shrink-0 rounded-md border border-white/10 bg-white/[.06] px-3 py-1 text-[10px] font-black uppercase tracking-[.1em] text-zinc-200 transition hover:border-white/20 hover:bg-white/[.1] disabled:opacity-40"
        >
          {busy ? "…" : "ask"}
        </button>
      </div>
      {notice && <p className={`mt-1 font-mono text-[10px] font-semibold ${tone}`}>{notice.text}</p>}
    </div>
  );
}

export function QuestionsPanel() {
  const questions = usePoll(useCallback(() => api.questions(20), []), 1000);
  const refresh = questions.refresh;
  const onChanged = useCallback(() => { void refresh(); }, [refresh]);
  const rows = useMemo(
    () => [...(questions.data ?? [])].sort((a, b) => b.created_t - a.created_t),
    [questions.data],
  );
  const open = rows.filter(q => q.status === "open").length;

  return (
    <section className="panel overflow-hidden">
      <div className="section-title border-b border-white/5 px-4 py-3">
        <span>Questions</span>
        <span className="muted">{open ? `${open} waiting on the wearer` : "asked · answered · suppressed"}</span>
      </div>
      <AskBar onAsked={onChanged} />
      <div className="max-h-[520px] divide-y divide-white/5 overflow-y-auto">
        {rows.map(q => <QuestionCard key={q.id} q={q} onAnswered={onChanged} />)}
        {rows.length === 0 && <p className="px-4 py-6 text-xs text-zinc-600">Nothing asked yet.</p>}
      </div>
    </section>
  );
}
