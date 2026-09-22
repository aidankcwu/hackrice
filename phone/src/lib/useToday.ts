"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError } from "./api";
import { toApiError } from "./today";
import type { Decision, Episode, Healthspan, Session, Status } from "./types";

/** IOS_SPEC TodayView: the backend is read every 30 s while the screen is in front, and on return. */
const POLL_MS = 30_000;

export interface TodayData {
  status: Status | null;
  /** The open session, `null` when none. */
  session: Session | null;
  healthspan: Healthspan | null;
  episodes: Episode[] | null;
  decisions: Decision[] | null;
  /** The first request that failed on the last read; the strip names its fix. */
  error: ApiError | null;
  /** False until the first read has answered, one way or the other. */
  loaded: boolean;
}

const INITIAL: TodayData = {
  status: null,
  session: null,
  healthspan: null,
  episodes: null,
  decisions: null,
  error: null,
  loaded: false,
};

/**
 * Today's four reads, polled. A failed read keeps the last good answer on screen
 * and reports the failure as `error`; the next good read clears it.
 */
export function useToday(): TodayData & { refresh: () => Promise<void> } {
  const [data, setData] = useState<TodayData>(INITIAL);
  const inFlight = useRef<Promise<void> | null>(null);

  const refresh = useCallback((): Promise<void> => {
    if (inFlight.current) return inFlight.current;
    const read = (async () => {
      const [status, healthspan, episodes, decisions] = await Promise.allSettled([
        api.status(),
        api.healthspan(),
        api.episodes(),
        api.decisions(50),
      ]);
      const failures: unknown[] = [status, healthspan, episodes, decisions]
        .filter((result) => result.status === "rejected")
        .map((result) => (result as PromiseRejectedResult).reason);

      // `/api/status` carries the open session with `elapsed_s`; a backend that
      // predates that field is asked `/api/session/current` instead.
      let session: Session | null | undefined;
      if (status.status === "fulfilled") {
        session = status.value.session;
        if (session === undefined) {
          try {
            session = await api.session();
          } catch (reason) {
            failures.push(reason);
          }
        }
      }

      setData((previous) => ({
        status: status.status === "fulfilled" ? status.value : previous.status,
        session: session === undefined ? previous.session : session,
        healthspan: healthspan.status === "fulfilled" ? healthspan.value : previous.healthspan,
        episodes: episodes.status === "fulfilled" ? episodes.value : previous.episodes,
        decisions: decisions.status === "fulfilled" ? decisions.value : previous.decisions,
        error: failures.length > 0 ? toApiError(failures[0]) : null,
        loaded: true,
      }));
    })().finally(() => {
      inFlight.current = null;
    });
    inFlight.current = read;
    return read;
  }, []);

  useEffect(() => {
    const inFront = () => document.visibilityState === "visible";
    const onVisibility = () => {
      if (inFront()) void refresh();
    };
    void refresh();
    const timer = window.setInterval(onVisibility, POLL_MS);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refresh]);

  return { ...data, refresh };
}
