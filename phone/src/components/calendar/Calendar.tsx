"use client";

import { useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Segment } from "@/components/Segment";
import { planFor, shortDate } from "@/lib/calendar";
import { useMonth } from "@/lib/useMonth";
import { Columns } from "./Columns";
import { DayStrip } from "./DayStrip";

type View = "day" | "week";

/**
 * The protocol laid over what happened, sized so a whole day fits one phone
 * screen. Day: the date, the two ceilings, the seven-lane strip. Week: seven
 * compact columns; a tap opens the day. Arrows or a swipe move through the month.
 */
export function Calendar() {
  const month = useMonth();
  const [view, setView] = useState<View>("day");
  const [picked, setPicked] = useState<number | null>(null);
  const touch = useRef<number | null>(null);

  const days = useMemo(() => month.month?.days ?? [], [month.month]);
  const plans = useMemo(() => days.map((_, i) => planFor(days, i, month.findings[i] ?? [])), [days, month.findings]);
  const last = days.length - 1;
  const index = picked ?? last;

  if (!month.loaded) return null;
  if (!month.month || index < 0) {
    return (
      <p className="type-body mt-2 text-muted">
        {month.error === "unreachable"
          ? "The Mac is not answering. The calendar appears once it does."
          : "The month appears once the backend serves it. Fixtures mode shows a seeded month."}
      </p>
    );
  }

  const step = view === "day" ? 1 : 7;
  const go = (delta: number) => setPicked(Math.max(0, Math.min(last, index + delta)));
  const weekFrom = Math.max(0, index - 6);
  const operating = month.operating[index];
  const range = view === "day" ? shortDate(days[index].date) : weekRange(days[weekFrom].date, days[index].date);

  return (
    <div
      onTouchStart={(e) => {
        touch.current = e.touches[0]?.clientX ?? null;
      }}
      onTouchEnd={(e) => {
        const start = touch.current;
        const end = e.changedTouches[0]?.clientX;
        touch.current = null;
        if (start === null || end === undefined) return;
        if (end - start > 60) go(-step);
        else if (start - end > 60) go(step);
      }}
    >
      <div className="mt-2 flex items-center gap-2">
        <Segment on={view === "day"} onClick={() => setView("day")}>
          Day
        </Segment>
        <Segment on={view === "week"} onClick={() => setView("week")}>
          Week
        </Segment>
        <div className="ml-auto flex items-center">
          <button type="button" aria-label={view === "day" ? "Previous day" : "Previous week"} disabled={index === 0} onClick={() => go(-step)} className="grid size-11 place-items-center rounded-full text-ink disabled:text-muted">
            <ChevronLeft size={22} strokeWidth={2} aria-hidden="true" />
          </button>
          <span className="type-secondary text-center font-semibold whitespace-nowrap text-ink tabular-nums" aria-live="polite">
            {range}
          </span>
          <button type="button" aria-label={view === "day" ? "Next day" : "Next week"} disabled={index === last} onClick={() => go(step)} className="grid size-11 place-items-center rounded-full text-ink disabled:text-muted">
            <ChevronRight size={22} strokeWidth={2} aria-hidden="true" />
          </button>
        </div>
      </div>

      {view === "day" ? (
        <>
          <p className="type-number m-0 mt-1 text-ink">
            Cognition {Math.round(operating.cognition)}% <span className="text-muted">·</span> Body {Math.round(operating.body)}%
          </p>
          {!operating.calibration.ready ? <p className="type-caption m-0 text-muted tabular-nums">{sentence(operating.calibration.label)}</p> : null}
          <div className="mt-3">
            <DayStrip key={days[index].date} plan={plans[index]} />
          </div>
        </>
      ) : (
        <div className="mt-3">
          <Columns
            columns={days.slice(weekFrom, index + 1).map((day, n) => ({
              index: weekFrom + n,
              date: day.date,
              plan: plans[weekFrom + n],
              cognition: month.operating[weekFrom + n].cognition,
              body: month.operating[weekFrom + n].body,
            }))}
            height={440}
            cap={20}
            selected={index}
            onPick={(i) => {
              setPicked(i);
              setView("day");
            }}
          />
        </div>
      )}
    </div>
  );
}

/** "calibrating, 4 of 7 days" as a sentence: first letter up. */
function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** "Sep 16 to 22", or "Aug 28 to Sep 3" across a month's end. */
function weekRange(from: string, to: string): string {
  const a = new Date(`${from}T12:00:00`);
  const b = new Date(`${to}T12:00:00`);
  const start = a.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const end = a.getMonth() === b.getMonth() ? String(b.getDate()) : b.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return `${start} to ${end}`;
}
