"use client";

import { Fragment, useState } from "react";
import { X } from "lucide-react";
import { HOUR_MARKS, LANES, yAt, type Bar, type DayPlan, type LaneId } from "@/lib/calendar";
import { LANE_ICONS, SEGMENT_FILL } from "./lanes";

/** The whole day on one phone screen: a 28 px night cap, then 06:00 to 24:00. */
const HEIGHT = 512;
const CAP = 28;
const HOUR_W = 20;
const LANE_W = 16;
const LANE_GAP = 5;
const LANES_X = HOUR_W + 6;
const LANES_W = LANES.length * LANE_W + (LANES.length - 1) * LANE_GAP;
/** Labels of neighbouring bars keep this much room between them. */
const LABEL_STEP = 16;

const LANE_INDEX = new Map<LaneId, number>(LANES.map((lane, i) => [lane.id, i]));
const laneX = (lane: LaneId) => LANES_X + (LANE_INDEX.get(lane) ?? 0) * (LANE_W + LANE_GAP);
const y = (minutes: number) => yAt(minutes, HEIGHT, CAP);

/** Label heights for the bars (time order): at their bar, nudged down when two sit close, back up at the bottom edge. */
function placeLabels(bars: Bar[]): number[] {
  const out: number[] = [];
  for (const bar of bars) out.push(out.length ? Math.max(y(bar.time), out[out.length - 1] + LABEL_STEP) : y(bar.time));
  for (let i = out.length - 1; i >= 0; i--) out[i] = Math.min(out[i], i === out.length - 1 ? HEIGHT - 6 : out[i + 1] - LABEL_STEP);
  return out;
}

/**
 * Day view. Seven thin lanes, an icon over each; each lane's protocol windows
 * as soft segments, green where the rule was met, grey where not, a red X at
 * the end of a window missed without a violation. Violations are red bars
 * across all lanes with a short label; tapping one shows the rule.
 */
export function DayStrip({ plan }: { plan: DayPlan }) {
  const [open, setOpen] = useState<string | null>(null);
  const labels = placeLabels(plan.bars);
  const openBar = plan.bars.find((b) => b.id === open);

  return (
    <div>
      <div className="relative h-5">
        {LANES.map((lane) => {
          const Icon = LANE_ICONS[lane.id];
          return (
            <span key={lane.id} role="img" aria-label={lane.name} title={lane.name} className="absolute top-0 grid h-5 place-items-center text-muted" style={{ left: laneX(lane.id), width: LANE_W }}>
              <Icon size={14} strokeWidth={2} aria-hidden="true" />
            </span>
          );
        })}
      </div>

      <div className={`relative mt-1 ${plan.sick ? "opacity-60" : ""}`} style={{ height: HEIGHT }} onClick={() => setOpen(null)}>
        {HOUR_MARKS.map((h) => (
          <Fragment key={h}>
            <span aria-hidden="true" className="absolute text-right text-[11px] leading-none text-muted tabular-nums" style={{ left: 0, width: HOUR_W, top: y(h * 60) - 6 }}>
              {h}
            </span>
            <span aria-hidden="true" className="absolute h-px bg-line" style={{ left: LANES_X - 2, right: 0, top: y(h * 60) }} />
          </Fragment>
        ))}

        {plan.segments.map((s) => (
          <span
            key={`${s.lane}-${s.start}`}
            aria-hidden="true"
            className={`absolute rounded-full ${SEGMENT_FILL[s.state]}`}
            style={{ left: laneX(s.lane), width: LANE_W, top: y(s.start), height: Math.max(4, y(s.end) - y(s.start)) }}
          />
        ))}

        {plan.ticks.map((t, n) => (
          <Fragment key={`${t.lane}-${t.time}-${n}`}>
            <span
              aria-hidden="true"
              className={`absolute rounded-full ${t.kind === "waking" ? "h-[1.5px] bg-muted" : "h-[2px] bg-ink"}`}
              style={t.kind === "waking" ? { left: laneX(t.lane) + 4, width: LANE_W - 8, top: y(t.time) } : { left: laneX(t.lane) + 2, width: LANE_W - 4, top: y(t.time) - 1 }}
            />
            {t.minutes !== undefined ? (
              <span aria-hidden="true" className="absolute text-center text-[9px] leading-none font-semibold text-ink tabular-nums" style={{ left: laneX(t.lane) - 3, width: LANE_W + 6, top: y(t.time) + 3 }}>
                {t.minutes}
              </span>
            ) : null}
          </Fragment>
        ))}

        {plan.misses.map((m) => (
          <X
            key={`${m.lane}-${m.time}`}
            size={11}
            strokeWidth={3}
            aria-hidden="true"
            className="absolute text-cost"
            style={{ left: laneX(m.lane) + LANE_W / 2 - 5.5, top: y(m.time) - 5.5 }}
          />
        ))}

        {plan.bars.map((bar, n) => {
          const by = y(bar.time);
          const ly = labels[n];
          const top = Math.min(by, ly) - 9;
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
              style={{ left: LANES_X - 3, right: 0, top, height: Math.abs(ly - by) + 18 }}
            >
              <span aria-hidden="true" className="absolute rounded-full bg-cost" style={{ left: 0, width: LANES_W + 6, top: by - top - 1.5, height: 3 }} />
              <span
                aria-hidden="true"
                className="absolute text-[12px] leading-none font-semibold whitespace-nowrap text-cost tabular-nums"
                style={{ left: LANES_W + 12, top: ly - top - 6 }}
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
      </ul>
    </div>
  );
}

/** The rule behind a bar, never its effect. Below the bar, or above it near the bottom edge. */
function RuleBox({ bar }: { bar: Bar }) {
  const by = y(bar.time);
  const below = by < HEIGHT - 96;
  return (
    <div
      role="note"
      onClick={(e) => e.stopPropagation()}
      className="absolute z-20 rounded-[12px] bg-cost-soft px-3 py-2"
      style={{ left: LANES_X, right: 0, ...(below ? { top: by + 10 } : { bottom: HEIGHT - by + 10 }) }}
    >
      <p className="type-secondary m-0 font-semibold text-cost">{bar.what}</p>
      <p className="type-secondary m-0 text-text">Rule: {bar.ruleText}</p>
    </div>
  );
}
