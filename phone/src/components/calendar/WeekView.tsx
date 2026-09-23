import type { Day } from "@/lib/month/types";
import type { Operating } from "@/lib/operating";
import { WINDOWS, type Finding } from "@/lib/rules";

/** Compressed clock: the night is short, the day gets the room. */
const NIGHT_PX = 4; // per hour, 0:00 to 6:00
const DAY_PX = 17; // per hour, 6:00 to 24:00
const HEIGHT = 6 * NIGHT_PX + 18 * DAY_PX;

function y(minutes: number): number {
  const h = minutes / 60;
  return h <= 6 ? h * NIGHT_PX : 6 * NIGHT_PX + (h - 6) * DAY_PX;
}

const TICKS = [6, 12, 18];

interface Column {
  day: Day;
  index: number;
  operating: Operating;
  findings: Finding[];
}

/**
 * Seven narrow columns, the same bands compressed: sleep, the eating window and
 * the caffeine window as rails, reds and ambers as dots at their times. Each
 * day's two ceilings on top, last night's sleep as a bar underneath. A tap
 * opens the day.
 */
export function WeekView({ columns, selected, onPick }: { columns: Column[]; selected: number; onPick: (index: number) => void }) {
  const maxSleep = 9 * 60;
  return (
    <div>
      <div className="flex items-end gap-1">
        <span className="w-6 shrink-0" />
        {columns.map(({ day, index, operating }) => {
          const date = new Date(`${day.date}T12:00:00`);
          return (
            <button
              key={day.date}
              type="button"
              onClick={() => onPick(index)}
              aria-label={`Open ${date.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}`}
              className={`min-w-0 flex-1 rounded-[10px] py-1 text-center ${index === selected ? "bg-surface-2" : ""}`}
            >
              <span className="type-caption block text-muted">{date.toLocaleDateString("en-US", { weekday: "short" })}</span>
              <span className="type-secondary block font-semibold text-ink tabular-nums">{date.getDate()}</span>
              <span className="type-caption block text-text tabular-nums">{Math.round(operating.cognition)}</span>
              <span className="type-caption block text-muted tabular-nums">{Math.round(operating.body)}</span>
            </button>
          );
        })}
      </div>
      <p className="type-caption m-0 mt-1 text-right text-muted">Top: cognition % · under it: body %</p>

      <div className="mt-2 flex gap-1">
        <div className="relative w-6 shrink-0" style={{ height: HEIGHT }} aria-hidden="true">
          {TICKS.map((h) => (
            <span key={h} className="type-caption absolute right-0 -translate-y-1/2 text-muted tabular-nums" style={{ top: y(h * 60) }}>
              {String(h).padStart(2, "0")}
            </span>
          ))}
        </div>
        {columns.map(({ day, index, findings }) => (
          <button
            key={day.date}
            type="button"
            onClick={() => onPick(index)}
            aria-label={`${day.date}: ${findings.filter((f) => f.tone === "violation").length} outside the protocol`}
            className={`relative min-w-0 flex-1 overflow-hidden rounded-[10px] ${day.type === "sick" ? "opacity-50" : ""} ${index === selected ? "bg-surface" : ""}`}
            style={{ height: HEIGHT }}
          >
            {TICKS.map((h) => (
              <span key={h} aria-hidden="true" className="absolute inset-x-0 h-px bg-line" style={{ top: y(h * 60) }} />
            ))}
            <Rail left={3} start={0} end={WINDOWS.wake} />
            <Rail left={3} start={WINDOWS.sleepStart} end={1440} />
            <Rail left={9} start={WINDOWS.eatingStart} end={WINDOWS.eatingEnd} />
            <Rail left={15} start={WINDOWS.wake} end={WINDOWS.caffeineEnd} />
            {findings
              .filter((f) => f.tone === "violation" || f.tone === "watch")
              .map((f, n) => (
                <span
                  key={`${f.rule}-${f.time}-${n}`}
                  aria-hidden="true"
                  className={`absolute size-[7px] -translate-y-1/2 rounded-full ${f.tone === "violation" ? "bg-cost" : "bg-watch"}`}
                  style={{ top: y(f.time), left: f.tone === "violation" ? 24 : 33 }}
                />
              ))}
          </button>
        ))}
      </div>

      <div className="mt-2 flex items-end gap-1">
        <span className="type-caption w-6 shrink-0 text-muted">Sleep</span>
        {columns.map(({ day, index }) => {
          const hours = day.sleep.minutes / 60;
          return (
            <button key={day.date} type="button" onClick={() => onPick(index)} className="flex min-w-0 flex-1 flex-col items-center gap-1" aria-label={`Sleep ${hours.toFixed(1)} hours`}>
              <span className="flex h-12 w-3 items-end overflow-hidden rounded-full bg-band" aria-hidden="true">
                <span
                  className={`block w-full rounded-full ${day.sleep.minutes < 7 * 60 ? "bg-cost" : "bg-ink"}`}
                  style={{ height: `${Math.min(1, day.sleep.minutes / maxSleep) * 100}%` }}
                />
              </span>
              <span className="type-caption text-muted tabular-nums">{hours.toFixed(1)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function Rail({ left, start, end }: { left: number; start: number; end: number }) {
  return <span aria-hidden="true" className="absolute w-1 rounded-full bg-band" style={{ left, top: y(start), height: y(end) - y(start) }} />;
}
