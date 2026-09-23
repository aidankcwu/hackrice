"use client";

import { useMemo } from "react";
import { Button } from "@/components/ui";
import type { Calibration, Measured } from "@/lib/operating";
import type { MonthState } from "@/lib/useMonth";
import { ceilingLines, yourDay } from "@/lib/yourDay";

/**
 * Under the hero: the two ceilings in two equal columns with a hairline between,
 * the calibration or measured line under them, "How it's computed", then "Your
 * day" in three to five plain sentences. Drawn from the month's last day; absent
 * when the backend serves no month.
 */
export function TheDay({ month, onHow }: { month: MonthState; onHow: () => void }) {
  const data = useMemo(() => {
    const days = month.month?.days;
    if (!days?.length) return null;
    const index = days.length - 1;
    const operating = month.operating[index];
    return {
      ceilings: ceilingLines(operating),
      note: calibrationNote(operating.calibration, operating.measured),
      sentences: yourDay(days, index, month.findings, month.operating),
    };
  }, [month.month, month.findings, month.operating]);

  if (!data) return null;
  return (
    <>
      <section aria-label="Your two ceilings" className="mt-section">
        <div className="grid grid-cols-2">
          {data.ceilings.map((c, n) => (
            <div key={c.label} className={n ? "border-l border-hairline pl-4" : "pr-4"}>
              <p className="type-secondary m-0 text-muted">{c.label}</p>
              <p className="type-number m-0 mt-1 text-ink">
                {Math.round(c.value)}
                <span aria-hidden="true">%</span>
                <span className="sr-only"> percent of your ceiling</span>
              </p>
              <p className="type-caption m-0 mt-1 text-muted">{c.reason}</p>
            </div>
          ))}
        </div>
        {data.note ? <p className="type-caption m-0 mt-3 text-muted tabular-nums">{data.note}</p> : null}
        <div className="mt-2">
          <Button variant="tertiary" onClick={onHow}>
            How it’s computed
          </Button>
        </div>
      </section>

      <section aria-labelledby="your-day-title" className="mt-section">
        <h2 id="your-day-title" className="type-section m-0 text-ink">
          Your day
        </h2>
        <p className="type-body m-0 mt-2 text-text">{data.sentences.join(" ")}</p>
      </section>
    </>
  );
}

/** "Calibrating, 4 of 7 days" until the best week is known; then this morning against it. */
function calibrationNote(calibration: Calibration, measured: Measured): string | null {
  if (!calibration.ready) return `${calibration.label.charAt(0).toUpperCase()}${calibration.label.slice(1)}`;
  const parts: string[] = [];
  if (measured.cognition !== null) parts.push(`PVT ${Math.round(measured.cognition)}%`);
  if (measured.body !== null) parts.push(`HRV ${Math.round(measured.body)}%`);
  return parts.length ? `Measured this morning: ${parts.join(", ")}` : null;
}
