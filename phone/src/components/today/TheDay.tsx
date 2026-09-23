"use client";

import { useMemo } from "react";
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
    return {
      ceilings: ceilingLines(month.operating[index]),
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
