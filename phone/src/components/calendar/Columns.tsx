import type { ReactNode } from "react";
import { LANES, yAt, type DayPlan } from "@/lib/calendar";
import type { RuleId } from "@/lib/rules";
import { SEGMENT_FILL } from "./lanes";

export interface Column {
  index: number;
  date: string;
  plan: DayPlan;
  cognition: number;
  body: number;
}

const HEADER_H = 66;
const HOUR_W = 18;
const MARKS = [6, 12, 18, 24] as const;
const BADGE_STEP = 16;
const LANE_INDEX = new Map(LANES.map((lane, i) => [lane.id, i]));

/** A number on a red bar; the same rule shares one number. */
export function NumberBadge({ n, size = 16 }: { n: number; size?: number }) {
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full bg-cost text-[10px] leading-none font-bold text-page tabular-nums"
      style={{ width: size, height: size }}
    >
      {n}
    </span>
  );
}

/**
 * Days side by side, one compact column each: the seven lanes as thin stripes
 * (green where the rule was met, grey where not), the red bars at their time of
 * day, the two ceilings on top. `onPick` makes a column open its day;
 * `numbers` puts a number on every bar (Analysis).
 */
export function Columns({
  columns,
  height,
  cap,
  selected,
  onPick,
  numbers,
}: {
  columns: Column[];
  height: number;
  cap: number;
  selected?: number;
  onPick?: (index: number) => void;
  numbers?: Partial<Record<RuleId, number>>;
}) {
  const narrow = columns.length > 7;
  const y = (m: number) => yAt(m, height, cap);
  return (
    <div>
      <div className="flex gap-[2px]">
        <div aria-hidden="true" className="relative shrink-0" style={{ width: HOUR_W, marginTop: HEADER_H, height }}>
          {MARKS.map((h) => (
            <span key={h} className="absolute right-1 text-[10px] leading-none text-muted tabular-nums" style={{ top: y(h * 60) - 5 }}>
              {h}
            </span>
          ))}
        </div>
        {columns.map((column) => {
          const date = new Date(`${column.date}T12:00:00`);
          const weekday = date.toLocaleDateString("en-US", { weekday: narrow ? "narrow" : "short" });
          const inner: ReactNode = (
            <>
              <span className="block text-center" style={{ height: HEADER_H }}>
                <span className="block pt-1 text-[11px] leading-4 text-muted">{weekday}</span>
                <span className="block text-[13px] leading-5 font-semibold text-ink tabular-nums">{date.getDate()}</span>
                <span className="block text-[11px] leading-4 text-text tabular-nums">{Math.round(column.cognition)}</span>
                <span className="block text-[11px] leading-4 text-muted tabular-nums">{Math.round(column.body)}</span>
              </span>
              <MiniDay plan={column.plan} height={height} y={y} numbers={numbers} narrow={narrow} />
            </>
          );
          const className = `block min-w-0 flex-1 rounded-[8px] ${column.index === selected ? "bg-surface-2" : ""}`;
          const name = date.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
          return onPick ? (
            <button
              key={column.date}
              type="button"
              onClick={() => onPick(column.index)}
              aria-label={`Open ${name}: cognition ${Math.round(column.cognition)}%, body ${Math.round(column.body)}%, ${column.plan.bars.length} outside the protocol`}
              className={className}
            >
              {inner}
            </button>
          ) : (
            <div key={column.date} className={className} aria-label={`${name}: ${column.plan.bars.map((b) => b.label).join(", ") || "nothing outside the protocol"}`} role="group">
              {inner}
            </div>
          );
        })}
      </div>
      <p className="type-caption m-0 mt-2 text-muted">Over each day: cognition, then body, as % of ceiling.</p>
    </div>
  );
}

function MiniDay({
  plan,
  height,
  y,
  numbers,
  narrow,
}: {
  plan: DayPlan;
  height: number;
  y: (m: number) => number;
  numbers?: Partial<Record<RuleId, number>>;
  narrow: boolean;
}) {
  const step = 100 / LANES.length;
  const badges: { id: string; n: number; top: number }[] = [];
  if (numbers) {
    for (const bar of plan.bars) {
      const n = numbers[bar.rule];
      if (n === undefined) continue;
      const prev = badges[badges.length - 1];
      badges.push({ id: bar.id, n, top: prev ? Math.max(y(bar.time), prev.top + BADGE_STEP) : y(bar.time) });
    }
    // Back up from the bottom edge when a late cluster runs past it.
    for (let i = badges.length - 1; i >= 0; i--) {
      badges[i].top = Math.min(badges[i].top, i === badges.length - 1 ? height - 8 : badges[i + 1].top - BADGE_STEP);
    }
  }
  return (
    <span aria-hidden="true" className={`relative block ${plan.sick ? "opacity-60" : ""}`} style={{ height }}>
      {MARKS.slice(0, 3).map((h) => (
        <span key={h} className="absolute inset-x-0 h-px bg-line" style={{ top: y(h * 60) }} />
      ))}
      {plan.segments.map((s) => (
        <span
          key={`${s.lane}-${s.start}`}
          className={`absolute rounded-[2px] ${SEGMENT_FILL[s.state]}`}
          style={{
            left: `calc(${(LANE_INDEX.get(s.lane) ?? 0) * step}% + 0.5px)`,
            width: `calc(${step}% - 1px)`,
            top: y(s.start),
            height: Math.max(2, y(s.end) - y(s.start)),
          }}
        />
      ))}
      {plan.bars.map((bar) => (
        <span key={bar.id} className="absolute inset-x-0 h-[2px] rounded-full bg-cost" style={{ top: y(bar.time) - 1 }} />
      ))}
      {badges.map((b) => (
        <span key={b.id} className="absolute left-1/2 -translate-x-1/2 -translate-y-1/2" style={{ top: b.top }}>
          <NumberBadge n={b.n} size={narrow ? 14 : 16} />
        </span>
      ))}
    </span>
  );
}
