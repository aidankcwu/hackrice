"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiResult } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { GOALS } from "@/lib/score/types";
import type { DashboardData, Goal } from "@/lib/score/types";
import { BrianDashboard } from "./BrianDashboard";

export interface BrianLiveProps {
  /** Server-rendered first payload; the client re-polls `GET /api/score?goal=` after mount. */
  initial: DashboardData;
  /** Poll interval in ms (default 5000). */
  intervalMs?: number;
}

const STORAGE_KEY = "brian.goal";

const isGoal = (v: unknown): v is Goal => typeof v === "string" && GOALS.some((g) => g.value === v);

const looksLikeDashboard = (v: unknown): v is DashboardData =>
  typeof v === "object" && v !== null && "person" in v && "layers" in v && "source" in v;

/** Throws on any failure so usePoll keeps the last good payload instead of swapping in a fixture. */
async function fetchScore(goal: Goal): Promise<ApiResult<DashboardData>> {
  const res = await fetch(`/api/score?goal=${encodeURIComponent(goal)}`, {
    cache: "no-store",
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) throw new Error(`/api/score ${res.status}`);
  const json: unknown = await res.json();
  if (!looksLikeDashboard(json)) throw new Error("/api/score: unexpected payload");
  // `/api/score` has no fixture behind it (loader.ts): every payload is live.
  return { data: json, mock: false };
}

export function BrianLive({ initial, intervalMs = 5000 }: BrianLiveProps) {
  const [goal, setGoal] = useState<Goal>(initial.person.goal);
  const [updating, setUpdating] = useState(false);

  // localStorage is read after mount so the server HTML and the first client
  // render agree; an explicit `?goal=` in the URL outranks the remembered one.
  useEffect(() => {
    try {
      if (new URLSearchParams(window.location.search).has("goal")) return;
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (isGoal(stored)) setGoal(stored);
    } catch {
      // Storage can be unavailable (private mode, blocked); the server goal stands.
    }
  }, []);

  const poll = usePoll(
    useCallback(() => fetchScore(goal), [goal]),
    intervalMs,
  );
  const { refresh } = poll;

  // usePoll only re-reads its fn on the next tick; a goal change should not
  // wait for it. The ref remembers which goal was last requested so the
  // mount run (and StrictMode's replay of it) does not double-fetch.
  const requested = useRef<Goal>(initial.person.goal);
  useEffect(() => {
    if (requested.current === goal) return;
    requested.current = goal;
    let cancelled = false;
    setUpdating(true);
    void refresh().finally(() => {
      if (!cancelled) setUpdating(false);
    });
    return () => {
      cancelled = true;
    };
  }, [goal, refresh]);

  return <BrianDashboard data={poll.data ?? initial} updating={updating} />;
}
