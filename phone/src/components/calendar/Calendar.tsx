"use client";

import { useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { analyze } from "@/lib/analysis";
import { bandsFor, headerFor, itemsFor } from "@/lib/calendar";
import { useMonth } from "@/lib/useMonth";
import { AnalysisPanel } from "./AnalysisPanel";
import { DayHeaderView, DayTimeline } from "./DayView";
import { WeekView } from "./WeekView";

type View = "day" | "week";

/**
 * The protocol laid over what happened. Day: a 24-hour timeline, the windows as
 * soft labelled bands, reality on top. Week: seven compressed columns. Arrows
 * or a swipe move between the month's days.
 */
export function Calendar() {
  const month = useMonth();
  const [view, setView] = useState<View>("day");
  const [picked, setPicked] = useState<number | null>(null);
  const [scope, setScope] = useState<"week" | "month">("week");
  const touch = useRef<number | null>(null);

  const days = useMemo(() => month.month?.days ?? [], [month.month]);
  const last = days.length - 1;
  const index = picked ?? last;

  const dayData = useMemo(() => {
    if (!month.month || index < 0) return null;
    const day = days[index];
    const findings = month.findings[index] ?? [];
    return {
      day,
      header: headerFor(days, index, month.operating[index]),
      bands: bandsFor(day),
      ...itemsFor(day, findings, days[index + 1]),
    };
  }, [month.month, month.findings, month.operating, days, index]);

  if (!month.loaded) return null;
  if (!month.month || !dayData) {
    return (
      <p className="type-body mt-section text-muted">
        {month.error === "unreachable"
          ? "The Mac is not answering. The calendar appears once it does."
          : "The month appears once the backend serves it. Fixtures mode shows a seeded month."}
      </p>
    );
  }

  const step = view === "day" ? 1 : 7;
  const go = (delta: number) => setPicked(Math.max(0, Math.min(last, index + delta)));
  const weekFrom = Math.max(0, index - 6);
  const columns = days.slice(weekFrom, index + 1).map((day, n) => ({
    day,
    index: weekFrom + n,
    operating: month.operating[weekFrom + n],
    findings: month.findings[weekFrom + n],
  }));
  const range =
    view === "day"
      ? dayData.header.short
      : `${new Date(`${days[weekFrom].date}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" })} to ${dayData.header.short}`;

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
          <span className="type-secondary min-w-[88px] text-center font-semibold text-ink tabular-nums" aria-live="polite">
            {range}
          </span>
          <button type="button" aria-label={view === "day" ? "Next day" : "Next week"} disabled={index === last} onClick={() => go(step)} className="grid size-11 place-items-center rounded-full text-ink disabled:text-muted">
            <ChevronRight size={22} strokeWidth={2} aria-hidden="true" />
          </button>
        </div>
      </div>

      {view === "day" ? (
        <>
          <div className="mt-4">
            <DayHeaderView header={dayData.header} />
          </div>
          <div className="mt-section">
            <DayTimeline bands={dayData.bands} items={dayData.items} frames={dayData.frames} sick={dayData.day.type === "sick"} />
          </div>
        </>
      ) : (
        <div className="mt-4">
          <div className="mb-3 flex items-center gap-2">
            <Segment on={scope === "week"} onClick={() => setScope("week")}>
              This week
            </Segment>
            <Segment on={scope === "month"} onClick={() => setScope("month")}>
              {days.length} days
            </Segment>
          </div>
          <AnalysisPanel
            month={scope === "month"}
            analysis={
              scope === "month"
                ? analyze(days, month.findings, month.operating, 0, last, `${days.length} days`)
                : analyze(days, month.findings, month.operating, weekFrom, index, index === last ? "This week" : `The week to ${dayData.header.short}`)
            }
          />
          <div className="mt-section">
            <WeekView
              columns={columns}
              selected={index}
              onPick={(i) => {
                setPicked(i);
                setView("day");
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function Segment({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`type-secondary min-h-9 rounded-full px-4 font-semibold ${on ? "bg-ink text-page" : "bg-surface-2 text-text"}`}
    >
      {children}
    </button>
  );
}
