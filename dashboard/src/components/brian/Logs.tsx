"use client";
import { useCallback, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { T } from "@/lib/tokens";
import type { RecapSummary, Session } from "@/lib/types";
import { H2, Panel } from "./Panel";

/* The Logs index: one row per saved recap, newest first, titled by when it was
 * made. Opening a row goes to `/logs/<id>`.
 *
 * Recording lives here too, because starting a session and reading what it
 * produced are the same task. Ending a session generates its recap in the same
 * gesture -- `POST /api/session/end` only closes the window, so the chaining is
 * done client-side rather than by changing an endpoint other code depends on.
 *
 * The recap is not spoken. It is a written report, and the glasses already
 * spoke during the session. */

const dateTitle = (t: number) =>
  new Date(t * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });

const duration = (s: number): string => {
  const mins = Math.round(s / 60);
  if (mins >= 60) return `${Math.floor(mins / 60)} h ${mins % 60} min`;
  return mins <= 1 ? `${Math.max(1, Math.round(s))} s` : `${mins} min`;
};

export function Logs() {
  const [busy, setBusy] = useState<"" | "starting" | "ending" | "generating">("");
  const [error, setError] = useState("");

  // usePoll wants an ApiResult; these endpoints answer with the value itself and
  // already degrade to null/[] on failure, so `mock` is always false here.
  const session = usePoll<Session | null>(
    useCallback(async () => ({ data: await api.sessionCurrent(), mock: false }), []), 3000);
  const logs = usePoll<RecapSummary[]>(
    useCallback(async () => ({ data: await api.recaps(20), mock: false }), []), 5000);
  const rows = logs.data ?? [];
  const open = session.data != null && session.data.ended_t === null;

  const start = useCallback(async () => {
    setError(""); setBusy("starting");
    try { await api.sessionStart(""); await session.refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "could not start"); }
    finally { setBusy(""); }
  }, [session]);

  const end = useCallback(async () => {
    setError(""); setBusy("ending");
    try {
      await api.sessionEnd();
      await session.refresh();
      // `POST /api/session/end` generates the recap itself, in the background.
      // Asking for one here as well produced two entries per session, ~10 ms
      // apart. Poll the list instead until the new one lands.
      setBusy("generating");
      const before = rows.length;
      for (let i = 0; i < 20; i++) {
        await new Promise((r) => setTimeout(r, 500));
        const next = await api.recaps(20);
        if (next.length > before) break;
      }
      await logs.refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "could not end session"); }
    finally { setBusy(""); }
  }, [session, logs, rows.length]);

  return (
    <Panel id="logs" labelledBy="logs-title">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <H2 id="logs-title" sub="Recorded sessions, newest first">Logs</H2>
        <div className="flex items-center gap-3">
          {open && (
            <span className="tnum text-sm" style={{ color: T.muted }}>
              Recording since {new Date((session.data as Session).started_t * 1000)
                .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          <button
            onClick={open ? end : start}
            disabled={busy !== ""}
            className="rounded-full px-4 py-2 text-sm font-semibold disabled:opacity-50"
            style={{ background: open ? T.costSoft : T.ink, color: open ? T.cost : T.bg }}
          >
            {busy === "starting" ? "Starting…"
              : busy === "ending" ? "Ending…"
              : busy === "generating" ? "Writing the report…"
              : open ? "End Session" : "Start Session"}
          </button>
        </div>
      </div>

      {error && <p className="m-0 mb-3 text-sm" style={{ color: T.cost }}>{error}</p>}

      {rows.length === 0 ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          No sessions yet. Start one, wear the glasses, then end it to get a report.
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col p-0">
          {rows.map((row) => (
            <li key={row.id} style={{ borderTop: `1px solid ${T.line}` }}>
              <Link
                href={`/logs/${row.id}`}
                className="tile flex items-center justify-between gap-4 px-2 py-3 no-underline"
              >
                <span className="min-w-0">
                  <span className="block text-base font-semibold" style={{ color: T.ink }}>
                    {dateTitle(row.generated_at)}
                  </span>
                  <span className="tnum block text-sm" style={{ color: T.muted }}>
                    {duration(row.duration_s)}
                  </span>
                </span>
                <span className="shrink-0 text-sm" style={{ color: T.muted }}>View</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
