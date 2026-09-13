"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { T, fmtH } from "@/lib/tokens";
import { H2, Panel } from "./Panel";

/**
 * "Next best minutes" (screens.md §1.6) — the levers ranked by healthy-life
 * hours per minute, and how often the wearer actually does each one.
 *
 * The adherence line is drawn only when the payload carries a `p_adherence` for
 * that lever (`levers_personalized`, the engine's Thompson sampling over the
 * wearer's own log). With no log there is no percentage, and the row simply has
 * one fewer line — a made-up "71 %" would be the worst kind of fabrication,
 * because it is a claim about the wearer (R1).
 */

export interface NextBestRow {
  key: string;
  /** The engine's `action` string, e.g. "Vigorous bursts: 1.4 → 4.4 min/day". */
  action: string;
  /** Minutes it costs; 0 reads "costs no time". */
  time: number;
  /** Healthy-life hours gained. */
  gain: number;
  /** `levers_personalized[].p_adherence`, 0–1. Absent when the wearer has no log yet. */
  pAdherence?: number;
}

/** One placed block, remembered per browser so the list shows what was already planned. */
export interface Placed {
  key: string;
  action: string;
  /** ISO timestamp it was placed. */
  at: string;
}

const STORAGE_KEY = "bryan.placed";
const UNDO_MS = 8000;

function readPlaced(): Placed[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (p): p is Placed =>
        typeof p === "object" && p !== null && typeof (p as Placed).key === "string" && typeof (p as Placed).action === "string",
    );
  } catch {
    // Private mode, blocked storage, or a stale shape: nothing is placed.
    return [];
  }
}

function writePlaced(list: Placed[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  } catch {
    // A plan that cannot be remembered is still a plan that was made; the toast
    // already said so and the next load simply starts clean.
  }
}

export interface NextBestProps {
  rows: readonly NextBestRow[];
}

export function NextBest({ rows }: NextBestProps) {
  const [placed, setPlaced] = useState<Placed[]>([]);
  const [toast, setToast] = useState<Placed | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // localStorage is read after mount so the server HTML and the first client
  // render agree.
  useEffect(() => {
    setPlaced(readPlaced());
  }, []);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const place = useCallback((row: NextBestRow) => {
    const entry: Placed = { key: row.key, action: row.action, at: new Date().toISOString() };
    setPlaced((prev) => {
      const next = [...prev.filter((p) => p.key !== row.key), entry];
      writePlaced(next);
      return next;
    });
    setToast(entry);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), UNDO_MS);
  }, []);

  const undo = useCallback((entry: Placed) => {
    setPlaced((prev) => {
      const next = prev.filter((p) => p.key !== entry.key);
      writePlaced(next);
      return next;
    });
    setToast(null);
    if (timer.current) clearTimeout(timer.current);
  }, []);

  return (
    <Panel id="plan" labelledBy="next-best-title">
      <H2 id="next-best-title" sub="Ranked by healthy-life hours per minute — and how often you actually do it.">
        Next best minutes
      </H2>

      {rows.length === 0 ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          No levers yet. They appear once today has measured doses to improve on.
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col p-0">
          {rows.map((row, i) => {
            const done = placed.some((p) => p.key === row.key);
            return (
              <li
                key={row.key}
                className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3"
                style={i === 0 ? undefined : { borderTop: `1px solid ${T.line}` }}
              >
                <div className="min-w-0 flex-1">
                  <div className="text-base font-semibold" style={{ color: T.ink }}>
                    {row.action}
                  </div>
                  <div className="text-sm" style={{ color: T.muted }}>
                    {row.time === 0 ? "costs no time" : `${row.time} minutes`}
                  </div>
                  {row.pAdherence !== undefined && (
                    <div className="tnum text-xs" style={{ color: T.muted }}>
                      you do this {Math.round(row.pAdherence * 100)} % of the time
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <span className="tnum text-base font-bold" style={{ color: T.earn }}>
                    {fmtH(row.gain)}
                  </span>
                  {i === 0 && (
                    <button
                      type="button"
                      onClick={() => place(row)}
                      disabled={done}
                      className="tile inline-flex h-11 items-center rounded-full px-5 text-sm font-semibold disabled:cursor-default"
                      style={{
                        background: done ? T.earnSoft : T.bg,
                        color: done ? T.earn : T.ink,
                      }}
                    >
                      {done ? "Placed" : "Place It"}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <div aria-live="polite" className="mt-3">
        {toast && (
          <div
            className="flex flex-wrap items-center justify-between gap-3 rounded-2xl px-4 py-3"
            style={{ background: T.earnSoft, color: T.earn }}
          >
            <span className="min-w-0 text-sm font-medium">Placed: {toast.action}</span>
            <button
              type="button"
              onClick={() => undo(toast)}
              className="inline-flex h-11 items-center rounded-full px-4 text-sm font-semibold underline"
              style={{ color: T.earn }}
            >
              Undo
            </button>
          </div>
        )}
      </div>
    </Panel>
  );
}
