"use client";

import { useState, type ReactNode } from "react";
import { RefreshCw } from "lucide-react";
import { Button, Chip, MEANING_ICONS, STROKE } from "@/components/ui";
import { airChip, buildLedger, watchedMinutes } from "@/lib/today";
import { useMonth } from "@/lib/useMonth";
import { PULL_THRESHOLD, usePullToRefresh } from "@/lib/usePullToRefresh";
import { useToday } from "@/lib/useToday";
import { ConnectSheet } from "./ConnectSheet";
import { HowSheet } from "./HowSheet";
import { Hero } from "./Hero";
import { Ledger } from "./Ledger";
import { MindCheck } from "./MindCheck";
import { StatusStrip } from "./StatusStrip";
import { TheDay } from "./TheDay";

const EMPTY = "Put the glasses on. Counting starts the moment the camera is up.";

/**
 * Today, top to bottom: status strip with the air chip · primary button · hero
 * panel · the two ceilings and "Your day" · the mind check card · the log
 * (IOS_SPEC TodayView). Web substitution for the primary button: until Job 1 the
 * glasses app owns the stream, so the button reflects the backend's session
 * instead of starting one. Live session: "Watching · 14 min". None: "Connect
 * glasses", which opens the steps. Backend unreadable: the status strip becomes
 * the problem row with "Try again".
 *
 * `query` carries the fixtures-mode screenshot params onto the detail links.
 */
export function Today({ query }: { query: string }) {
  const today = useToday();
  const month = useMonth();
  const { pull, busy } = usePullToRefresh(today.refresh);
  const [connecting, setConnecting] = useState(false);
  const [how, setHow] = useState(false);

  // Nothing is drawn before the backend's first answer (at most the 15 s timeout).
  if (!today.loaded) return null;

  const { status, session, healthspan, episodes, decisions, error } = today;
  const entries = episodes || decisions ? buildLedger(episodes ?? [], decisions ?? []) : null;
  const empty = !error && !session && entries !== null && entries.length === 0;
  const lastDay = month.month?.days.at(-1) ?? null;
  const air = airChip(lastDay);
  // The clock of the month's last day; the phone's own clock once the month has answered with none.
  const until = lastDay ? lastDay.until : month.loaded ? null : undefined;

  let primary: ReactNode;
  if (session) {
    primary = (
      <div role="status">
        <Button>Watching · {watchedMinutes(session, status)} min</Button>
      </div>
    );
  } else {
    primary = <Button onClick={() => setConnecting(true)}>Connect glasses</Button>;
  }

  return (
    <>
      <div
        aria-hidden="true"
        className="flex items-center justify-center overflow-hidden text-muted"
        style={{ height: busy ? 44 : pull }}
      >
        <RefreshCw
          size={20}
          strokeWidth={STROKE}
          className={busy ? "animate-spin" : undefined}
          style={busy ? undefined : { opacity: Math.min(pull / PULL_THRESHOLD, 1), transform: `rotate(${pull * 3}deg)` }}
        />
      </div>

      <div className="mt-2 flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <StatusStrip status={status} session={session} error={error} onRetry={() => void today.refresh()} />
        </div>
        {air ? (
          <div className="shrink-0 pt-1">
            <Chip tone={air.tone} icon={MEANING_ICONS.air}>
              {air.text}
            </Chip>
          </div>
        ) : null}
      </div>

      <div className="mt-section">{primary}</div>

      {healthspan ? (
        <div className="mt-section">
          <Hero healthspan={healthspan} />
        </div>
      ) : null}

      <TheDay month={month} onHow={() => setHow(true)} />

      {until !== undefined ? <MindCheck until={until} query={query} /> : null}

      {/* Nothing seen yet: the instruction sits where the log will be, the button above it. */}
      {empty || (entries && entries.length > 0) ? (
        <Ledger entries={entries ?? []} query={query} empty={EMPTY} />
      ) : null}

      <ConnectSheet open={connecting} onClose={() => setConnecting(false)} />
      <HowSheet open={how} onClose={() => setHow(false)} />
    </>
  );
}
