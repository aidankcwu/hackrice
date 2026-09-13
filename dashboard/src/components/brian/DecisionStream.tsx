"use client";
import { useCallback, useMemo } from "react";
import { Quote } from "lucide-react";
import { api } from "@/lib/api";
import { T } from "@/lib/tokens";
import { usePoll } from "@/lib/usePoll";
import type { Decision, DecisionAction } from "@/lib/types";
import type { PinRow } from "@/lib/score/types";
import {
  actionLabel,
  actionTypes,
  annotateLine,
  clockTime,
  confidenceWidth,
  decisionState,
  effectHours,
  feedLine,
  insightLines,
  linkedPin,
  newestFirst,
  spokenText,
  type DecisionState,
} from "@/lib/score/narrative";

const POLL_MS = 2_000;
const LIMIT = 50;
const FRAME_W = 288;
const FRAME_H = 120;

/**
 * Chip fills. Only the two data colours are allowed to carry meaning
 * (design-system/brian/MASTER.md): a chip that moves healthy-life hours takes
 * the soft green or red, everything else is grey on white. Ink on the soft
 * fills, because the tinted text of the reference sits just under 4.5:1.
 */
const CHIP: Readonly<Record<DecisionAction["type"], { bg: string; color: string }>> = {
  annotate: { bg: T.surface2, color: T.ink },
  log_insight: { bg: T.surface2, color: T.ink },
  watch: { bg: T.bg, color: T.muted },
  speak: { bg: T.header, color: T.bg },
  ask: { bg: T.bg, color: T.ink },
  nothing: { bg: T.bg, color: T.muted },
};

function ActionChip({ type }: { type: DecisionAction["type"] }) {
  const { bg, color } = CHIP[type];
  return (
    <span
      className="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold"
      style={{ background: bg, color, border: bg === T.bg ? `1px solid ${T.line}` : undefined }}
    >
      {actionLabel(type)}
    </span>
  );
}

/** The one-word outcome. "Spoke" is the only highlighted state; silent rows stay grey on purpose. */
function StateChip({ state, reason }: { state: DecisionState; reason: string | null }) {
  if (state === "dropped")
    return (
      <span className="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold" style={{ background: T.costSoft, color: T.ink }}>
        Dropped{reason ? ` · ${reason}` : ""}
      </span>
    );
  if (state === "spoke")
    return (
      <span className="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold" style={{ background: T.header, color: T.bg }}>
        <Quote size={12} strokeWidth={2.5} aria-hidden="true" /> Spoke
      </span>
    );
  return (
    <span
      className="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold"
      style={{ background: T.bg, color: T.muted, border: `1px solid ${T.line}` }}
    >
      Silent
    </span>
  );
}

/**
 * Confidence as a short bar. The figure is printed beside it, so the bar itself
 * is decorative. A row that carries no confidence shows an em dash and an empty
 * track — never a full bar standing in for a number nobody reported.
 */
function Confidence({ value }: { value: number }) {
  const known = Number.isFinite(value);
  return (
    <span className="inline-flex items-center gap-2">
      <span className="block h-1.5 w-20 rounded-full" style={{ background: T.surface2 }} aria-hidden="true">
        <span className="block h-1.5 rounded-full" style={{ width: `${confidenceWidth(value) * 100}%`, background: T.ink }} />
      </span>
      <span className="tnum text-xs" style={{ color: T.muted }}>
        confidence {known ? value.toFixed(2) : "—"}
      </span>
    </span>
  );
}

/**
 * The annotation the row is about: the `annotate` line the reasoner wrote, and
 * where a pin sits at the same minute, the frame it annotated and the hours
 * that frame moved. Nothing is drawn that the payload did not supply.
 */
function Annotation({ d, pin }: { d: Decision; pin: PinRow | null }) {
  const line = annotateLine(d.actions);
  const insights = insightLines(d.actions);
  const spoken = spokenText(d.actions);
  const hours = pin ? effectHours(pin.effect) : null;
  const earn = pin?.kind === "earn";

  return (
    <div className="mt-3 flex flex-col gap-3 border-t pt-3" style={{ borderColor: T.line }}>
      <div>
        <div className="text-xs font-semibold uppercase tracking-wide" style={{ color: T.muted }}>
          Annotation written to the episode
        </div>
        <p className="m-0 mt-1 text-sm" style={{ color: line ? T.text : T.muted }}>
          {line ?? "— no annotate line on this decision"}
        </p>
      </div>

      {insights.length > 0 && (
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide" style={{ color: T.muted }}>
            Filed as an insight
          </div>
          <ul className="m-0 mt-1 flex list-none flex-col gap-1 p-0">
            {insights.map((insight, i) => (
              <li key={`${insight.category}-${i}`} className="text-sm" style={{ color: T.text }}>
                <span style={{ color: T.muted }}>{insight.category}</span> — {insight.text}
              </li>
            ))}
          </ul>
        </div>
      )}

      {spoken && (
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide" style={{ color: T.muted }}>
            {d.spoke ? "Said out the glasses" : "Proposed, then suppressed by the speech limiter"}
          </div>
          <p className="m-0 mt-1 text-sm" style={{ color: d.spoke ? T.ink : T.muted }}>
            &ldquo;{spoken}&rdquo;
          </p>
        </div>
      )}

      {pin ? (
        <figure className="m-0 overflow-hidden" style={{ background: T.bg, borderRadius: T.radiusPin, border: `1px solid ${T.line}` }}>
          {pin.img && (
            // Evidence frames come off the local backend at JPEG size already;
            // next/image would want a remotePatterns host that changes per
            // machine, so the plain element is used here as in Evidence.tsx.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={pin.img}
              alt={pin.seen}
              width={FRAME_W}
              height={FRAME_H}
              loading="lazy"
              decoding="async"
              className="h-28 w-full object-cover"
            />
          )}
          <figcaption className="flex flex-wrap items-baseline gap-x-3 gap-y-1 p-3">
            <span className="tnum text-xs font-semibold" style={{ color: T.muted }}>
              {pin.time}
            </span>
            <span className="text-sm font-semibold" style={{ color: T.ink }}>
              {pin.seen}
            </span>
            {hours !== null && (
              <span className="rounded-full px-2.5 py-1 text-xs font-semibold" style={{ background: earn ? T.earnSoft : T.costSoft, color: T.ink }}>
                {earn ? "Earned" : "Cost"}
              </span>
            )}
            <span className="w-full text-sm" style={{ color: T.text }}>
              {pin.effect}
            </span>
            <span className="w-full text-sm" style={{ color: T.muted }}>
              Evidence grade {pin.grade} · matched to this decision on time, within ten minutes
            </span>
          </figcaption>
        </figure>
      ) : (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          {d.episode_id
            ? "No pinned frame at this minute — the episode it touched earned or cost nothing the engine scores."
            : "No episode behind this one, so there is no frame to show."}
        </p>
      )}
    </div>
  );
}

function Row({ d, pin }: { d: Decision; pin: PinRow | null }) {
  const state = decisionState(d);
  const types = actionTypes(d.actions);
  // Silent rows are dimmed, not hidden: "always writes, rarely speaks" is the
  // behaviour on show, so the quiet majority has to stay legible (muted text
  // clears 5.1:1 on the panel) rather than fade out.
  const headline = state === "silent" ? T.text : T.ink;

  return (
    <details
      className="tile group overflow-hidden"
      style={{ background: T.bg, borderRadius: T.radiusPin, border: state === "spoke" ? `1px solid ${T.ink}` : `1px solid ${T.line}` }}
    >
      {/* `list-none` hides the triangle everywhere but WebKit, which needs its own pseudo-element. */}
      <summary className="list-none p-4 [&::-webkit-details-marker]:hidden" aria-label={feedLine(d)}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="tnum text-sm font-semibold" style={{ color: T.muted }}>
            {clockTime(d.t)}
          </span>
          <span className="text-sm font-semibold" style={{ color: T.ink }}>
            {d.trigger}
          </span>
          <StateChip state={state} reason={d.drop_reason} />
          {/* The backend's `latency_ms` is nullable (`int | None`); an unrecorded
              one prints an em dash rather than a zero it did not measure. */}
          <span className="tnum ml-auto text-xs" style={{ color: T.muted }}>
            {typeof d.latency_ms === "number" ? `${d.latency_ms} ms` : "— ms"} · {d.model || "model not recorded"}
          </span>
        </div>
        <p className="m-0 mt-2 text-sm" style={{ color: headline }}>
          {d.interpretation || "No interpretation returned."}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
          {types.length > 0 ? types.map((type) => <ActionChip key={type} type={type} />) : <ActionChip type="nothing" />}
          <Confidence value={d.confidence} />
          <span className="ml-auto text-xs font-medium" style={{ color: T.muted }}>
            <span className="group-open:hidden">Show annotation</span>
            <span className="hidden group-open:inline">Hide annotation</span>
          </span>
        </div>
      </summary>
      <div className="px-4 pb-4">
        <Annotation d={d} pin={pin} />
      </div>
    </details>
  );
}

export interface DecisionStreamProps {
  /** Evidence pins from the server payload — the stream links to them, it never refetches them. */
  pins: readonly PinRow[];
  /** Poll interval in ms (default 2000). */
  intervalMs?: number;
}

/**
 * The decision stream: every T1 escalation, newest first, including the silent
 * ones (SPEC §6 — always writes, rarely speaks). Expanding a row shows the
 * annotation it wrote and, where a pinned frame sits at the same minute, what
 * that frame earned or cost. With no decisions yet the panel says so; it never
 * shows an example row (product rule R1).
 */
export function DecisionStream({ pins, intervalMs = POLL_MS }: DecisionStreamProps) {
  const poll = usePoll(useCallback(() => api.decisions(LIMIT), []), intervalMs);
  const rows = useMemo(() => newestFirst(poll.data ?? []), [poll.data]);
  const spoken = useMemo(() => rows.filter((d) => d.spoke).length, [rows]);

  return (
    <section aria-labelledby="decisions-title" className="min-w-0 p-6 md:p-8" style={{ background: T.surface, borderRadius: T.radius }}>
      <div className="mb-5">
        <h2 id="decisions-title" className="m-0 text-xl font-bold leading-tight" style={{ color: T.ink }}>
          What the reasoner decided
        </h2>
        <p className="m-0 mt-1 text-sm" style={{ color: T.muted }}>
          Every escalation is written down. Almost none of them are spoken aloud.
          {rows.length > 0 && (
            <span className="tnum">
              {" "}
              {rows.length} {rows.length === 1 ? "decision" : "decisions"}, {spoken} spoken.
            </span>
          )}
        </p>
        {/* `api.decisions` falls back to a fixture when the pipeline is
            unreachable. Rows that did not come off the running reasoner have to
            say so on the panel, not only in the console (product rule R1). */}
        {poll.mock && rows.length > 0 && (
          <p className="m-0 mt-2 text-sm font-medium" style={{ color: T.ink }}>
            Not live — the pipeline did not answer, so these rows are the built-in sample, not your reasoner.
          </p>
        )}
      </div>

      {rows.length === 0 ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          {poll.error
            ? `No decisions on screen — the pipeline did not answer (${poll.error}).`
            : "No decisions yet — the reasoner escalates only when a trigger fires."}
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-3 p-0">
          {rows.map((d) => (
            <li key={d.id} className="min-w-0">
              <Row d={d} pin={linkedPin(d, pins)} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
