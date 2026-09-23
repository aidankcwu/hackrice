"use client";

import { useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { LoadingState, SegmentedControl, STROKE } from "@/components/ui";
import { planFor, shortDate } from "@/lib/calendar";
import { useMonth } from "@/lib/useMonth";
import { Columns } from "./Columns";
import { DayStrip } from "./DayStrip";

type View = "day" | "week";

const VIEWS = [
  { id: "day", label: "Day" },
  { id: "week", label: "Week" },
] as const satisfies readonly { id: View; label: string }[];

/** How tall the week columns are: with their header and the sleep bar they fit under the day header on a 667 px screen. */
const WEEK_HEIGHT = 420;
const WEEK_CAP = 20;

/**
 * The protocol laid over what happened, sized so a whole day fits one phone
 * screen. Day: the date between two arrows, the two ceilings, the seven-lane
 * strip. Week: seven compact columns; a tap opens the day. Arrows or a swipe
 * move through the month.
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

  if (!month.loaded) return <LoadingState line="Loading the month…" blocks={2} />;
  if (!month.month || index < 0) {
    return (
      <p className="type-body m-0 mt-2 text-text">
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
      {/* One header row: [arrow] date [arrow] … [Day | Week]. */}
      <div className="mt-1 flex items-center">
        <ArrowButton direction="back" view={view} disabled={index === 0} onClick={() => go(-step)} />
        {/* 22 semibold from 390 px; a step smaller at 375, where the two 44 px arrows and the control leave 119 px for the date. */}
        <h2
          className={`m-0 min-w-0 truncate text-center whitespace-nowrap text-ink tabular-nums ${
            view === "day" ? "type-card-title min-[390px]:type-section" : "type-secondary font-semibold min-[390px]:type-card-title"
          }`}
          aria-live="polite"
        >
          {range}
        </h2>
        <ArrowButton direction="forward" view={view} disabled={index === last} onClick={() => go(step)} />
        <div className="ml-auto shrink-0 pl-2">
          <SegmentedControl options={VIEWS} value={view} onChange={setView} size={32} ariaLabel="View" />
        </div>
      </div>

      {view === "day" ? (
        <>
          <p className="type-secondary m-0 mt-1 text-text tabular-nums">
            Cognition {Math.round(operating.cognition)}% <span className="text-muted">·</span> Body {Math.round(operating.body)}%
            {!operating.calibration.ready ? <span className="text-muted"> · {sentence(operating.calibration.label)}</span> : null}
          </p>
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
              sleepMinutes: day.sleep.minutes,
            }))}
            height={WEEK_HEIGHT}
            cap={WEEK_CAP}
            width={44}
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

/** A 44 px arrow button; muted when there is nothing further that way. */
function ArrowButton({ direction, view, disabled, onClick }: { direction: "back" | "forward"; view: View; disabled: boolean; onClick: () => void }) {
  const Icon = direction === "back" ? ChevronLeft : ChevronRight;
  const label = `${direction === "back" ? "Previous" : "Next"} ${view}`;
  return (
    <button type="button" aria-label={label} disabled={disabled} onClick={onClick} className="pressable grid size-11 shrink-0 place-items-center rounded-full text-ink disabled:text-muted">
      <Icon size={24} strokeWidth={STROKE} aria-hidden="true" />
    </button>
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
