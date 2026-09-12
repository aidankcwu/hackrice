"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiResult } from "./api";

/**
 * Poll `fn` every `intervalMs`. Responses are stamped with a generation
 * counter so a slow, older request can never overwrite a newer result
 * (review finding: overlapping polls completing out of order).
 */
export function usePoll<T>(fn: () => Promise<ApiResult<T>>, intervalMs: number) {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const gen = useRef(0);
  const [data, setData] = useState<T>();
  const [mock, setMock] = useState(false);
  const [error, setError] = useState<string>();
  const refresh = useCallback(async () => {
    const mine = ++gen.current;
    try {
      const result = await fnRef.current();
      if (mine !== gen.current) return; // a newer poll already started
      setData(result.data);
      setMock(result.mock);
      setError(undefined);
    } catch (e) {
      if (mine !== gen.current) return;
      setError(e instanceof Error ? e.message : "Request failed");
    }
  }, []);
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(refresh, intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs, refresh]);
  return { data, mock, error, refresh };
}
