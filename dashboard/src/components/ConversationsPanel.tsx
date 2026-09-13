"use client";
import { useCallback, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import type { Conversation, ConversationSettled } from "@/lib/types";

const time = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

function SettledChips({ settled }: { settled: ConversationSettled }) {
  const chips: Array<[string, string]> = [];
  // The backend omits fields it never settled (`{}` or `{note}`), so a strict
  // null check rendered "confirmed no · count undefined" on every silent card.
  if (settled.confirmed != null) chips.push(["confirmed", settled.confirmed ? "yes" : "no"]);
  if (settled.count != null) chips.push(["count", String(settled.count)]);
  if (settled.food_type) chips.push(["food", settled.food_type]);
  if (settled.note) chips.push(["note", settled.note]);
  if (!chips.length) return null;
  return <div className="mt-2 flex flex-wrap gap-1 border-t border-white/5 pt-2">{chips.map(([key, value]) => <span key={key} className="inline-flex items-baseline gap-1 rounded border border-white/10 bg-white/[.04] px-1.5 py-[.05rem] text-[11px]"><span className="font-mono text-[9px] font-bold uppercase tracking-[.08em] text-zinc-500">{key}</span><span className="text-zinc-200">{value}</span></span>)}</div>;
}

function ConversationCard({ conversation }: { conversation: Conversation }) {
  const active = conversation.state === "active";
  return <article className={`px-4 py-3 ${active ? "bg-cyan-950/25" : ""}`}>
    <header className="flex flex-wrap items-center gap-x-2 gap-y-1"><span className="font-mono text-[11px] text-zinc-500">{time(conversation.opened_t)}</span><span className={`inline-flex items-center rounded-full border px-2 text-[10px] font-black uppercase tracking-[.12em] ${active ? "animate-pulse border-cyan-300/50 bg-cyan-400/25 text-cyan-100" : "border-white/10 bg-white/[.04] text-zinc-500"}`}>{active ? "active" : `closed${conversation.close_reason ? ` · ${conversation.close_reason}` : ""}`}</span><span className="ml-auto font-mono text-[10px] text-zinc-600">{conversation.id}</span></header>
    <p className="mt-1 text-[11px] text-zinc-500">{conversation.topic}</p>
    <div className="mt-2 space-y-1.5">{conversation.turns.map((turn, index) => <div key={`${turn.t}-${index}`} className={`flex ${turn.role === "wearer" ? "justify-end" : "justify-start"}`}><span className={`max-w-[88%] rounded-md px-2 py-1 text-xs leading-relaxed ${turn.role === "wearer" ? "bg-emerald-400/15 text-emerald-100" : "bg-violet-400/15 text-violet-100"}`}>{turn.kind === "question" && <span className="mr-1 rounded-full border border-violet-300/40 px-1 text-[9px] font-black">?</span>}{turn.text || "(silence)"}{turn.role === "wearer" && turn.heard === false && <span className="ml-2 rounded-full border border-amber-400/35 px-1.5 text-[9px] font-bold uppercase tracking-[.08em] text-amber-200">not heard</span>}</span></div>)}</div>
    {conversation.settled && <SettledChips settled={conversation.settled} />}
  </article>;
}

function OpenBar({ onOpened }: { onOpened: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ warning: boolean; text: string }>();
  const submit = async () => {
    const topic = text.trim();
    if (!topic || busy) return;
    setBusy(true); setNotice(undefined);
    try {
      const result = await api.openConversation(topic);
      setNotice(result.reason ? {warning:true, text:`active · ${result.reason}`} : {warning:false, text:`opened · ${result.id ?? ""}`});
      if (!result.reason) setText("");
      onOpened();
    } catch (error) { setNotice({warning:true, text:error instanceof Error ? error.message : "open failed"}); }
    finally { setBusy(false); }
  };
  return <div className="border-b border-white/5 px-4 py-2"><div className="flex items-center gap-2"><input value={text} onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === "Enter") void submit(); }} placeholder="Say something about…" aria-label="Conversation topic" className="min-w-0 flex-1 rounded-md border border-white/10 bg-black/30 px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-cyan-400/40 focus:outline-none" /><button type="button" onClick={() => void submit()} disabled={busy || !text.trim()} className="shrink-0 rounded-md border border-white/10 bg-white/[.06] px-3 py-1 text-[10px] font-black uppercase tracking-[.1em] text-zinc-200 transition hover:border-white/20 hover:bg-white/[.1] disabled:opacity-40">{busy ? "…" : "say"}</button></div>{notice && <p className={`mt-1 font-mono text-[10px] font-semibold ${notice.warning ? "text-amber-300" : "text-emerald-300"}`}>{notice.text}</p>}</div>;
}

export function ConversationsPanel() {
  const poll = usePoll(useCallback(() => api.conversations(20), []), 1000);
  const refresh = poll.refresh;
  const onOpened = useCallback(() => { void refresh(); }, [refresh]);
  const rows = useMemo(() => [...(poll.data ?? [])].sort((a, b) => b.opened_t - a.opened_t), [poll.data]);
  const active = rows.filter(row => row.state === "active").length;
  return <section className="panel overflow-hidden"><div className="section-title border-b border-white/5 px-4 py-3"><span>Conversations</span><span className="muted">{active} active · {rows.length-active} closed</span></div><OpenBar onOpened={onOpened} /><div className="max-h-[520px] divide-y divide-white/5 overflow-y-auto">{rows.map(row => <ConversationCard key={row.id} conversation={row} />)}{!rows.length && <p className="px-4 py-6 text-xs text-zinc-600">No conversations yet.</p>}</div></section>;
}
