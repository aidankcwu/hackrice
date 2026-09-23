"use client";

import { useState, type ReactNode } from "react";
import { RefreshCw } from "lucide-react";
import { buildLedger, watchedMinutes } from "@/lib/today";
import { PULL_THRESHOLD, usePullToRefresh } from "@/lib/usePullToRefresh";
import { useToday } from "@/lib/useToday";
import { ConnectSheet } from "./ConnectSheet";
import { HowSheet } from "./HowSheet";
import { Hero } from "./Hero";
import { Ledger } from "./Ledger";
import { StatusStrip } from "./StatusStrip";
import { TheDay } from "./TheDay";

const EMPTY = "Put the glasses on. Counting starts the moment the camera is up.";

/**
 * Today, top to bottom: status strip · primary button · hero · ledger (IOS_SPEC
 * TodayView). Web substitution for the primary button: until Job 1 the glasses
 * app owns the stream, so the button reflects the backend's session instead of
 * starting one. Live session: "Watching · 14 min". None: "Connect glasses", which
 * opens the steps. Backend unreadable: "Try again".
 *
 * `query` carries the fixtures-mode screenshot params onto the detail links.
 */
export function Today({ query }: { query: string }) {
  const today = useToday();
  const { pull, busy } = usePullToRefresh(today.refresh);
  const [connecting, setConnecting] = useState(false);
  const [how, setHow] = useState(false);

  // Nothing is drawn before the backend's first answer (at most the 15 s timeout).
  if (!today.loaded) return null;

  const { status, session, healthspan, episodes, decisions, error } = today;
  const entries = episodes || decisions ? buildLedger(episodes ?? [], decisions ?? []) : null;
  const empty = !error && !session && entries !== null && entries.length === 0;

  let primary: ReactNode;
  if (error) {
    primary = <PrimaryButton onClick={() => void today.refresh()}>Try again</PrimaryButton>;
  } else if (session) {
    primary = (
      <div role="status" className={`${PRIMARY} glass-prominent`}>
        Watching · {watchedMinutes(session, status)} min
      </div>
    );
  } else {
    primary = <PrimaryButton onClick={() => setConnecting(true)}>Connect glasses</PrimaryButton>;
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
          strokeWidth={2}
          className={busy ? "animate-spin" : undefined}
          style={busy ? undefined : { opacity: Math.min(pull / PULL_THRESHOLD, 1), transform: `rotate(${pull * 3}deg)` }}
        />
      </div>

      <div className="mt-2">
        <StatusStrip status={status} session={session} error={error} />
      </div>

      <div className="mt-section">{primary}</div>

      {healthspan ? (
        <div className="mt-section">
          <Hero healthspan={healthspan} />
        </div>
      ) : null}

      <TheDay onHow={() => setHow(true)} />

      {/* Nothing seen yet: the instruction sits where the log will be, the button above it. */}
      {empty || (entries && entries.length > 0) ? (
        <Ledger entries={entries ?? []} query={query} empty={EMPTY} />
      ) : null}

      <ConnectSheet open={connecting} onClose={() => setConnecting(false)} />
      <HowSheet open={how} onClose={() => setHow(false)} />
    </>
  );
}

/** The one primary control: a full-width capsule in the prominent glass. */
const PRIMARY =
  "type-body flex min-h-[50px] w-full items-center justify-center rounded-full px-6 py-2 text-center font-semibold";

function PrimaryButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} className={`${PRIMARY} glass-prominent`}>
      {children}
    </button>
  );
}
