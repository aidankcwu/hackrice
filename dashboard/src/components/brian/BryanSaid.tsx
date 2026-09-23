"use client";
import { useCallback, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useBackendUrl } from "@/lib/useBackendUrl";
import { T } from "@/lib/tokens";
import { usePoll } from "@/lib/usePoll";
import type { PinRow } from "@/lib/score/types";
import {
  OUTCOME_LABELS,
  buildNudgeFeed,
  heldBackSummary,
  type Nudge,
  type Outcome,
} from "@/lib/score/nudges";
import { Icon } from "./icons";
import { H2, Panel } from "./Panel";

const POLL_MS = 5_000;
const LIMIT = 50;
/** screens.md §1.3: max 6 rows on Today, then the Full day link. */
const TODAY_ROWS = 6;
/** The trigger frame thumb, 56 x 40. */
const THUMB_W = 56;
const THUMB_H = 40;

/**
 * Outcome chip fills. Only `Did it` is coloured — it is the one state that
 * moved healthy-life hours; `Didn't` stays grey rather than red, because
 * nudges.md rule 5 forbids shame, and `Pending` takes the track grey.
 */
const OUTCOME_STYLE: Readonly<Record<Outcome, { bg: string; color: string; border: boolean }>> = {
  did: { bg: T.earnSoft, color: T.ink, border: false },
  didnt: { bg: T.bg, color: T.muted, border: true },
  pending: { bg: T.surface2, color: T.ink, border: false },
};

function OutcomeChip({ outcome }: { outcome: Outcome }) {
  const { bg, color, border } = OUTCOME_STYLE[outcome];
  return (
    <span
      className="inline-flex shrink-0 items-center rounded-full px-2.5 py-1 text-xs font-semibold"
      style={{ background: bg, color, border: border ? `1px solid ${T.line}` : undefined }}
    >
      {OUTCOME_LABELS[outcome]}
    </span>
  );
}

/**
 * The trigger frame. Frames are served by the local backend at JPEG size
 * already and the host changes per machine, so next/image's remotePatterns
 * cannot be pinned — the plain element is used here as in Evidence.tsx. With no
 * frame the slot keeps its 56 x 40 so the rows stay aligned, and draws the
 * layer's own icon instead of a fabricated picture.
 */
function Frame({ nudge }: { nudge: Nudge }) {
  const src = useBackendUrl(nudge.frame);
  return (
    <span
      className="flex shrink-0 items-center justify-center overflow-hidden"
      style={{ width: THUMB_W, height: THUMB_H, background: T.surface2, borderRadius: 8 }}
    >
      {nudge.frame ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt={nudge.seen ?? nudge.trigger}
          width={THUMB_W}
          height={THUMB_H}
          loading="lazy"
          decoding="async"
          className="h-full w-full object-cover"
        />
      ) : (
        <Icon name="eye" size={16} strokeWidth={1.75} color={T.muted} />
      )}
    </span>
  );
}

/**
 * One row — a row, not a card (screens.md §1.3): time, the 56 x 40 frame, the
 * sentence in curly quotes, the outcome chip, and the muted citation. A line
 * the limiter held back prints the sentence it proposed in muted text with the
 * reason beside it, which is law 4: restraint is visible.
 */
function Row({ nudge }: { nudge: Nudge }) {
  const said = nudge.said;
  return (
    <li
      className="flex min-w-0 items-start gap-3 py-3"
      style={{ borderTop: `1px solid ${T.line}` }}
    >
      <span className="tnum w-11 shrink-0 pt-1 text-sm font-semibold" style={{ color: T.muted }}>
        {nudge.time}
      </span>
      <Frame nudge={nudge} />
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-base" style={{ color: nudge.spoke ? T.text : T.muted }}>
          {said === null ? (
            nudge.seen ?? nudge.trigger
          ) : (
            <>
              &ldquo;{said}&rdquo;
            </>
          )}
        </span>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs" style={{ color: T.muted }}>
          {nudge.category !== null && <span>{nudge.category}</span>}
          {nudge.evidence !== null && <span>{nudge.evidence}</span>}
          {!nudge.spoke && nudge.reason !== null && <span>held back — {nudge.reason}</span>}
        </span>
      </span>
      {nudge.spoke && <OutcomeChip outcome={nudge.outcome} />}
    </li>
  );
}

export interface BryanSaidProps {
  /** Evidence pins from the server payload — the log links to them, it never refetches them. */
  pins: readonly PinRow[];
  /** Minutes past local midnight the payload was built at; outcome windows never read past it. */
  nowMinute: number;
  /** Poll interval in ms (default 5000). */
  intervalMs?: number;
}

/**
 * What Bryan said (screens.md §1.3) — the ear log: every time it spoke today,
 * what it saw, and what happened next, with the held-back count beside it.
 *
 * Model names, latencies and confidence figures are absent on purpose
 * (checklist.md: they live only in the Pipeline drawer). With no decisions yet
 * the panel says so; it never passes an example line off as something Bryan
 * said (product rule R1).
 */
export function BryanSaid({ pins, nowMinute, intervalMs = POLL_MS }: BryanSaidProps) {
  const [full, setFull] = useState(false);
  const poll = usePoll(
    useCallback(() => api.decisions(LIMIT), []),
    intervalMs,
  );
  const feed = useMemo(
    () => buildNudgeFeed(poll.data ?? [], pins, { now: nowMinute }),
    [poll.data, pins, nowMinute],
  );

  const capped = !full && feed.spoken.length > TODAY_ROWS;
  const rows = capped ? feed.spoken.slice(0, TODAY_ROWS) : feed.spoken;
  const summary = heldBackSummary(feed);

  return (
    <Panel id="said" labelledBy="said-title">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <H2
          id="said-title"
          sub={`Every time it spoke in your ear today, what it saw, and what happened next. Held back ${feed.held.length}.`}
        >
          What Bryan said
        </H2>
        {capped && (
          <button
            type="button"
            onClick={() => setFull(true)}
            className="tile inline-flex h-11 shrink-0 items-center rounded-full px-4 text-sm font-medium"
            style={{ background: T.bg, color: T.ink, border: 0 }}
          >
            Full day
          </button>
        )}
      </div>

      {poll.mock && feed.spoken.length + feed.held.length > 0 && (
        <p className="m-0 mb-3 text-sm font-medium" style={{ color: T.ink }}>
          Seeded — this build runs with NEXT_PUBLIC_MOCK=1, so these lines are the built-in sample, not you.
        </p>
      )}

      {rows.length === 0 ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          {poll.error
            ? "Put the glasses on. Bryan starts counting light, people, and air the moment the camera is up."
            : "Bryan said nothing today. Silence is the default; it speaks at most 6 times a day."}
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col p-0" aria-live="polite">
          {rows.map((nudge) => (
            <Row key={nudge.id} nudge={nudge} />
          ))}
        </ul>
      )}

      {summary && (
        <p className="m-0 mt-4 text-sm" style={{ color: T.muted }}>
          {summary} Open the Pipeline drawer for the full held-back list.
        </p>
      )}
      {feed.notReached > 0 && (
        <p className="m-0 mt-1 text-sm" style={{ color: T.muted }}>
          {feed.notReached} escalation{feed.notReached === 1 ? "" : "s"} never reached the reasoner, so nothing decided
          against speaking. The Pipeline drawer has them.
        </p>
      )}
    </Panel>
  );
}

export default BryanSaid;
