"use client";

import { Fragment, useState } from "react";
import { STROKE } from "@/components/ui";
import { X } from "lucide-react";
import { HOUR_MARKS, LANES, yAt, type Bar, type DayPlan, type LaneId } from "@/lib/calendar";
import { LANE_ICONS, SEGMENT_FILL } from "./lanes";

/** The whole day on one phone screen: a 28 px night cap, then 06:00 to 24:00, 512 px in all. */
const HEIGHT = 512;
const CAP = 28;
/** The hour labels' column ("24" at 13 px) and the gap after it. */
const HOUR_W = 22;
const LANE_W = 36;
const LANE_GAP = 6;
const LANES_X = HOUR_W + 8;
const LANES_W = LANES.length * LANE_W + (LANES.length - 1) * LANE_GAP;
const ICON = 18;
/** A violation's tap target is this tall, centred on its bar. */
const TAP = 44;
/** Labels of neighbouring bars keep this much room between them (13/18 text). */
const LABEL_STEP = 20;

const LANE_INDEX = new Map<LaneId, number>(LANES.map((lane, i) => [lane.id, i]));
const laneX = (lane: LaneId) => LANES_X + (LANE_INDEX.get(lane) ?? 0) * (LANE_W + LANE_GAP);
const y = (minutes: number) => yAt(minutes, HEIGHT, CAP);

/** Label heights for the bars (time order): at their bar, nudged down when two sit close, back up at the bottom edge. */
function placeLabels(bars: Bar[]): number[] {
  const out: number[] = [];
  for (const bar of bars) out.push(out.length ? Math.max(y(bar.time), out[out.length - 1] + LABEL_STEP) : y(bar.time));
  for (let i = out.length - 1; i >= 0; i--) out[i] = Math.min(out[i], i === out.length - 1 ? HEIGHT - 9 : out[i + 1] - LABEL_STEP);
  return out;
}

/**
 * Day view. Seven lanes 36 wide, an icon over each; each lane's protocol
 * windows as outlined boxes, good where the rule was met, grey where not, a
 * red X at the end of a window missed without a violation. Ticks mark what
 * happened inside a lane. Violations are 3 px red bars across all lanes with a
 * short label at the right edge; tapping one opens the rule under the bar.
 */
export function DayStrip({ plan }: { plan: DayPlan }) {
  const [open, setOpen] = useState<string | null>(null);
  const labels = placeLabels(plan.bars);
  const openBar = plan.bars.find((b) => b.id === open);

  return (
    <div>
      <div className="relative" style={{ height: ICON }}>
        {LANES.map((lane) => {
          const Icon = LANE_ICONS[lane.id];
          return (
            <span key={lane.id} role="img" aria-label={lane.name} title={lane.name} className="absolute top-0 grid place-items-center text-muted" style={{ left: laneX(lane.id), width: LANE_W, height: ICON }}>
              <Icon size={ICON} strokeWidth={STROKE} aria-hidden="true" />
            </span>
          );
        })}
      </div>

      <div className={`relative mt-1 ${plan.sick ? "opacity-60" : ""}`} style={{ height: HEIGHT }} onClick={() => setOpen(null)}>
        {HOUR_MARKS.map((h) => (
          <Fragment key={h}>
            <span aria-hidden="true" className="type-caption absolute text-right text-muted tabular-nums" style={{ left: 0, width: HOUR_W, top: y(h * 60) - 9 }}>
              {h}
            </span>
            <span aria-hidden="true" className="absolute h-px bg-hairline" style={{ left: LANES_X, right: 0, top: y(h * 60) }} />
          </Fragment>
        ))}

        {plan.segments.map((s) => (
          <span
            key={`${s.lane}-${s.start}`}
            aria-hidden="true"
            className={`absolute box-border rounded-[6px] ${SEGMENT_FILL[s.state]}`}
            style={{ left: laneX(s.lane), width: LANE_W, top: y(s.start), height: Math.max(6, y(s.end) - y(s.start)) }}
          />
        ))}

        {plan.ticks.map((t, n) => (
          <Fragment key={`${t.lane}-${t.time}-${n}`}>
            <span
              aria-hidden="true"
              className={`absolute h-[2px] w-2 rounded-full ${t.kind === "waking" ? "bg-muted" : "bg-ink"}`}
              style={{ left: laneX(t.lane) + (t.minutes === undefined ? LANE_W / 2 - 4 : 4), top: y(t.time) - 1 }}
            />
            {t.minutes !== undefined ? (
              <span aria-hidden="true" className="type-tab absolute text-ink tabular-nums" style={{ left: laneX(t.lane) + 14, width: LANE_W - 14, top: y(t.time) - 7 }}>
                {t.minutes}
              </span>
            ) : null}
          </Fragment>
        ))}

        {plan.misses.map((m) => (
          <X
            key={`${m.lane}-${m.time}`}
            size={12}
            strokeWidth={2.5}
            aria-hidden="true"
            className="absolute text-bad"
            style={{ left: laneX(m.lane) + LANE_W / 2 - 6, top: y(m.time) - 6 }}
          />
        ))}

        {plan.bars.map((bar, n) => {
          const by = y(bar.time);
          const ly = labels[n];
          const top = Math.min(by, ly) - TAP / 2;
          return (
            <button
              key={bar.id}
              type="button"
              aria-expanded={open === bar.id}
              aria-label={`${bar.label}, outside the protocol. Show the rule.`}
              onClick={(e) => {
                e.stopPropagation();
                setOpen(open === bar.id ? null : bar.id);
              }}
              className="absolute z-10"
              style={{ left: LANES_X, right: 0, top, height: Math.abs(ly - by) + TAP }}
            >
              <span aria-hidden="true" className="absolute rounded-full bg-bad" style={{ left: 0, width: LANES_W, top: by - top - 1.5, height: 3 }} />
              <span
                aria-hidden="true"
                className="type-chip absolute right-0 rounded-[4px] bg-page px-1 whitespace-nowrap text-bad tabular-nums"
                style={{ top: ly - top - 9 }}
              >
                {bar.label}
              </span>
            </button>
          );
        })}

        {openBar ? <RuleBox bar={openBar} /> : null}
      </div>

      <ul className="sr-only">
        {plan.segments.map((s) => (
          <li key={`${s.lane}-${s.start}`}>
            {s.label}: {s.state === "met" ? "met" : s.state === "missed" ? "missed" : "not met"}
          </li>
        ))}
        {plan.ticks.map((t, n) => (
          <li key={`tick-${t.lane}-${t.time}-${n}`}>{t.label}</li>
        ))}
      </ul>
    </div>
  );
}

/** The rule behind a bar, never its effect. Under the bar, or above it near the bottom edge. Fades in over 200 ms. */
function RuleBox({ bar }: { bar: Bar }) {
  const by = y(bar.time);
  const below = by < HEIGHT - 96;
  return (
    <div
      role="note"
      onClick={(e) => e.stopPropagation()}
      className="fade-in absolute z-20 rounded-tile bg-surface px-4 py-3"
      style={{ left: LANES_X, right: 0, ...(below ? { top: by + 10 } : { bottom: HEIGHT - by + 10 }) }}
    >
      <p className="type-secondary m-0 font-semibold text-bad">{bar.what}</p>
      <p className="type-secondary m-0 text-text">Rule: {bar.ruleText}</p>
    </div>
  );
}
