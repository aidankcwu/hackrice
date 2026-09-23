"use client";

import { useEffect, useMemo, useState } from "react";
import { ApiError, request } from "./api";
import type { Month } from "./month/types";
import { monthFindings, operatingFor, type Operating } from "./operating";
import type { Finding } from "./rules";

export interface MonthState {
  loaded: boolean;
  month: Month | null;
  /** Findings per day, same order as `month.days`. */
  findings: Finding[][];
  /** The two ceilings per day. */
  operating: Operating[];
  /** "missing" when the backend has no month to serve (live mode today). */
  error: "missing" | "unreachable" | null;
}

/**
 * The seeded month (`GET /api/month`, served from `fixtures/month/days.json` in
 * fixtures mode) with every day's findings and ceilings computed once.
 */
export function useMonth(): MonthState {
  const [month, setMonth] = useState<Month | null>(null);
  const [error, setError] = useState<MonthState["error"]>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let live = true;
    request<Month>("/api/month")
      .then((data) => {
        if (live) setMonth(data);
      })
      .catch((e: unknown) => {
        if (live) setError(e instanceof ApiError && e.status === null ? "unreachable" : "missing");
      })
      .finally(() => {
        if (live) setLoaded(true);
      });
    return () => {
      live = false;
    };
  }, []);

  const computed = useMemo(() => {
    if (!month) return { findings: [], operating: [] };
    const findings = monthFindings(month.days);
    return { findings, operating: month.days.map((_, i) => operatingFor(month.days, i, findings)) };
  }, [month]);

  return { loaded, month, error, ...computed };
}
