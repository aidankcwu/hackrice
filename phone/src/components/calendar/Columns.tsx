import type { ReactNode } from "react";
import { LANES, yAt, type DayPlan } from "@/lib/calendar";
import type { RuleId } from "@/lib/rules";
import { STRIPE_FILL } from "./lanes";

export interface Column {
  index: number;
  date: string;
  plan: DayPlan;
  cognition: number;
  body: number;
  /** Last night's sleep, minutes; draws the 6 px bar under the column when given. */
  sleepMinutes?: number;
}

/** Weekday 11/13, date 13/18, cognition 13/18, body 13/18, and 4 px over the lanes. */
const HEADER_H = 71;
const HOUR_W = 18;
const MARKS = [6, 12, 18, 24] as const;
const BADGE = 18;
const BADGE_STEP = 20;
/** The sleep bar: 6 px, full at 9 h. */
const SLEEP_H = 6;
const SLEEP_FULL_MIN = 9 * 60;
const LANE_INDEX = new Map(LANES.map((lane, i) => [lane.id, i]));

/** A number on a red bar; the same rule shares one number. */
export function NumberBadge({ n, size = BADGE }: { n: number; size?: number }) {
  return (
    <span className="type-tab grid shrink-0 place-items-center rounded-full bg-bad text-page tabular-nums" style={{ width: size, height: size }}>
      {n}
    </span>
  );
}

/**
 * Days side by side, one compact column each: the seven lanes as thin stripes
 * (good where the rule was met, grey where not), the red bars at their time of
 * day, the two ceilings on top, last night's sleep as a bar at the bottom.
 * `onPick` makes a column open its day; `numbers` puts a number on every bar
 * (Analysis). `width` 44 lets the columns flex to fit the screen; 32 fixes them
 * for a scrolling row. `caption={false}` leaves the caption to the caller, which
 * renders `ColumnsCaption` outside its scroller so the caption wraps to the screen;
 * `hours={false}` leaves the hour axis to the caller too, which pins `HourAxis`
 * beside its scroller so the hours never scroll away.
 */
export function Columns({
  columns,
  height,
  cap,
  selected,
  onPick,
  numbers,
  width = 44,
  caption = true,
  hours = true,
}: {
  columns: Column[];
  height: number;
  cap: number;
  selected?: number;
  onPick?: (index: number) => void;
  numbers?: Partial<Record<RuleId, number>>;
  width?: 32 | 44;
  caption?: boolean;
  hours?: boolean;
}) {
  const narrow = width === 32;
  const y = (m: number) => yAt(m, height, cap);
  const hasSleep = columns.some((c) => c.sleepMinutes !== undefined);
  const sizing = narrow ? "w-8 shrink-0" : "min-w-0 flex-1 max-w-[44px]";
  return (
    <div>
      <div className={`flex ${narrow ? "gap-2" : "gap-2 min-[390px]:gap-3"}`}>
        {hours ? <HourAxis height={height} cap={cap} /> : null}
        {columns.map((column) => {
          const date = new Date(`${column.date}T12:00:00`);
          const weekday = date.toLocaleDateString("en-US", { weekday: narrow ? "narrow" : "short" });
          const inner: ReactNode = (
            <>
              <span className="block text-center" style={{ height: HEADER_H }}>
                <span className="type-tab block text-muted">{weekday}</span>
                <span className="type-caption block font-semibold text-ink tabular-nums">{date.getDate()}</span>
                <span className="type-caption block text-text tabular-nums">{Math.round(column.cognition)}</span>
                <span className="type-caption block text-muted tabular-nums">{Math.round(column.body)}</span>
              </span>
              <MiniDay plan={column.plan} height={height} y={y} numbers={numbers} />
              {hasSleep ? <SleepBar minutes={column.sleepMinutes} /> : null}
            </>
          );
          const className = `block rounded-[8px] ${sizing} ${column.index === selected ? "outline-2 -outline-offset-2 outline-ink" : ""}`;
          const name = date.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
          const sleep = column.sleepMinutes !== undefined ? `, slept ${hoursWord(column.sleepMinutes)}` : "";
          return onPick ? (
            <button
              key={column.date}
              type="button"
              onClick={() => onPick(column.index)}
              aria-current={column.index === selected ? "date" : undefined}
              aria-label={`Open ${name}: cognition ${Math.round(column.cognition)}%, body ${Math.round(column.body)}%, ${column.plan.bars.length} outside the protocol${sleep}`}
              className={`${className} pressable`}
            >
              {inner}
            </button>
          ) : (
            <div key={column.date} className={className} aria-label={`${name}: ${column.plan.bars.map((b) => b.label).join(", ") || "nothing outside the protocol"}${sleep}`} role="group">
              {inner}
            </div>
          );
        })}
      </div>
      {caption ? <ColumnsCaption sleep={hasSleep} /> : null}
    </div>
  );
}

/** 6, 12, 18, 24 down the left of the columns, level with their hairlines. */
export function HourAxis({ height, cap }: { height: number; cap: number }) {
  return (
    <div aria-hidden="true" data-chart-hours className="relative shrink-0" style={{ width: HOUR_W, marginTop: HEADER_H, height }}>
      {MARKS.map((h) => (
        <span key={h} className="type-tab absolute right-1 text-muted tabular-nums" style={{ top: yAt(h * 60, height, cap) - 6 }}>
          {h}
        </span>
      ))}
    </div>
  );
}

/** What the numbers over each column and the bar under it mean. */
export function ColumnsCaption({ sleep }: { sleep: boolean }) {
  return (
    <p data-chart-caption className="type-caption m-0 mt-2 text-muted">
      Over each day: cognition, then body, as % of ceiling.{sleep ? " Under it: last night's sleep, full at 9 h." : ""}
    </p>
  );
}

/** "7.5 h" for a screen reader. */
function hoursWord(minutes: number): string {
  return `${(Math.round(minutes / 6) / 10).toLocaleString("en-US")} h`;
}

/** Last night's sleep as a 6 px bar on the band track, full at 9 h. */
function SleepBar({ minutes }: { minutes?: number }) {
  const share = minutes === undefined ? 0 : Math.max(0, Math.min(1, minutes / SLEEP_FULL_MIN));
  return (
    <span aria-hidden="true" className="relative mt-1 block overflow-hidden rounded-full bg-band" style={{ height: SLEEP_H }}>
      <span className="absolute inset-y-0 left-0 rounded-full bg-ink" style={{ width: `${share * 100}%` }} />
    </span>
  );
}

function MiniDay({ plan, height, y, numbers }: { plan: DayPlan; height: number; y: (m: number) => number; numbers?: Partial<Record<RuleId, number>> }) {
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
      badges[i].top = Math.min(badges[i].top, i === badges.length - 1 ? height - BADGE / 2 : badges[i + 1].top - BADGE_STEP);
    }
  }
  return (
    <span aria-hidden="true" className={`relative block ${plan.sick ? "opacity-60" : ""}`} style={{ height }}>
      {MARKS.slice(0, 3).map((h) => (
        <span key={h} className="absolute inset-x-0 h-px bg-hairline" style={{ top: y(h * 60) }} />
      ))}
      {plan.segments.map((s) => (
        <span
          key={`${s.lane}-${s.start}`}
          className={`absolute rounded-[2px] ${STRIPE_FILL[s.state]}`}
          style={{
            left: `calc(${(LANE_INDEX.get(s.lane) ?? 0) * step}% + 0.5px)`,
            width: `calc(${step}% - 1px)`,
            top: y(s.start),
            height: Math.max(2, y(s.end) - y(s.start)),
          }}
        />
      ))}
      {plan.bars.map((bar) => (
        <span key={bar.id} className="absolute inset-x-0 h-[3px] rounded-full bg-bad" style={{ top: y(bar.time) - 1.5 }} />
      ))}
      {badges.map((b) => (
        <span key={b.id} className="absolute left-1/2 -translate-x-1/2 -translate-y-1/2" style={{ top: b.top }}>
          <NumberBadge n={b.n} size={BADGE} />
        </span>
      ))}
    </span>
  );
}
