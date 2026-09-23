"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError } from "./api";
import type { RowAction } from "./protocol";
import { toApiError } from "./today";
import type { ProtocolToday, ProtocolTodayItem } from "./types";

/** Read every 30 s while the screen is in front, and on return, like Today: `seen` and `missed` arrive from the backend. */
const POLL_MS = 30_000;

export interface ProtocolData {
  today: ProtocolToday | null;
  /** The last read or write that failed; the screen names its fix. */
  error: ApiError | null;
  /** False until the first read has answered, one way or the other. */
  loaded: boolean;
  /** The item a write is running on. */
  pending: string | null;
}

interface Write {
  item: ProtocolTodayItem;
  action: RowAction;
}

/**
 * Today's protocol, polled, and the three writes a row offers. A failed read keeps
 * the last good list; a failed write leaves the list as it was. `retry` repeats
 * whichever failed last.
 */
export function useProtocol(): ProtocolData & {
  act: (item: ProtocolTodayItem, action: RowAction) => Promise<void>;
  retry: () => void;
  /** Re-reads today's list (after Add item's POST). */
  refresh: () => Promise<void>;
} {
  const [data, setData] = useState<ProtocolData>({ today: null, error: null, loaded: false, pending: null });
  const inFlight = useRef<Promise<void> | null>(null);
  /** The write to repeat on "Try again"; `null` repeats the read. */
  const failedWrite = useRef<Write | null>(null);

  const refresh = useCallback((): Promise<void> => {
    if (inFlight.current) return inFlight.current;
    const read = api
      .protocolToday()
      .then((today) => setData((previous) => ({ ...previous, today, error: null, loaded: true })))
      .catch((reason: unknown) => {
        failedWrite.current = null;
        setData((previous) => ({ ...previous, error: toApiError(reason), loaded: true }));
      })
      .finally(() => {
        inFlight.current = null;
      });
    inFlight.current = read;
    return read;
  }, []);

  const act = useCallback(
    async (item: ProtocolTodayItem, action: RowAction): Promise<void> => {
      setData((previous) => ({ ...previous, pending: item.id }));
      try {
        let update: (items: ProtocolTodayItem[]) => ProtocolTodayItem[];
        let day: string | undefined;
        if (action === "delete") {
          await api.deleteProtocolItem(item.id);
          update = (items) => items.filter((row) => row.id !== item.id);
        } else {
          const { day: rowDay, ...row } = await (action === "done" ? api.markDone(item.id) : api.undo(item.id));
          day = rowDay;
          update = (items) => items.map((current) => (current.id === row.id ? row : current));
        }
        setData((previous) => ({
          ...previous,
          today: previous.today && { day: day ?? previous.today.day, items: update(previous.today.items) },
          error: null,
          pending: null,
        }));
      } catch (reason) {
        const error = toApiError(reason);
        // 404: the item is gone (deleted elsewhere); the fresh list says so.
        if (error.status === 404) {
          setData((previous) => ({ ...previous, pending: null }));
          void refresh();
          return;
        }
        failedWrite.current = { item, action };
        setData((previous) => ({ ...previous, error, pending: null }));
      }
    },
    [refresh],
  );

  const retry = useCallback(() => {
    const write = failedWrite.current;
    failedWrite.current = null;
    void (write ? act(write.item, write.action) : refresh());
  }, [act, refresh]);

  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    void refresh();
    const timer = window.setInterval(onVisibility, POLL_MS);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refresh]);

  return { ...data, act, retry, refresh };
}
