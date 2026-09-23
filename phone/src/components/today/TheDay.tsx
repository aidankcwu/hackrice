"use client";

import { useMemo } from "react";
import type { Calibration, Measured } from "@/lib/operating";
import { useMonth } from "@/lib/useMonth";
import { ceilingLines, yourDay } from "@/lib/yourDay";

/**
 * Under the hero: the two ceilings, each with its top reason, then "Your day"
 * in plain sentences. Drawn from the month's last day; absent when the backend
 * serves no month.
 */
export function TheDay({ onHow }: { onHow: () => void }) {
  const month = useMonth();
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
        {data.ceilings.map((c, n) => (
          <div key={c.label} className={`py-3 ${n ? "border-t-[0.5px] border-line" : ""}`}>
            <p className="type-body m-0 font-semibold text-ink tabular-nums">
              {c.label} · {Math.round(c.value)}%{n === 0 ? " of your ceiling" : ""}
            </p>
            <p className="type-secondary m-0 mt-0.5 text-muted">{c.reason}</p>
          </div>
        ))}
        {data.note ? <p className="type-caption m-0 mb-2 text-muted tabular-nums">{data.note}</p> : null}
        <button type="button" onClick={onHow} className="type-secondary min-h-11 font-semibold text-ink">
          How it’s computed
        </button>
      </section>

      <section aria-label="Your day" className="mt-section">
        <h2 className="type-title m-0 text-ink">Your day</h2>
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
