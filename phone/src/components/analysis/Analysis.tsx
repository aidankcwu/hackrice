"use client";

import { useMemo, useState } from "react";
import { Columns, NumberBadge } from "@/components/calendar/Columns";
import { Segment } from "@/components/Segment";
import { analyze } from "@/lib/analysis";
import { useMonth } from "@/lib/useMonth";

type Span = 7 | 14;

/**
 * Analysis: the days side by side with a number on every red bar (one number
 * per rule, so clusters show at a glance), then one card per number with what
 * it did to the body and to the mind, then the averages and the one lever.
 * No lines between things; the numbers do the pointing.
 */
export function Analysis() {
  const month = useMonth();
  const [span, setSpan] = useState<Span>(7);
  const days = useMemo(() => month.month?.days ?? [], [month.month]);
  const result = useMemo(() => {
    if (!days.length) return null;
    const last = days.length - 1;
    return analyze(days, month.findings, month.operating, Math.max(0, last - span + 1), last, span === 7 ? "This week" : "These 2 weeks");
  }, [days, month.findings, month.operating, span]);

  if (!month.loaded) return null;
  if (!result) {
    return (
      <p className="type-body mt-2 text-muted">
        {month.error === "unreachable"
          ? "The Mac is not answering. The analysis appears once it does."
          : "The analysis appears once the backend serves a month. Fixtures mode shows a seeded month."}
      </p>
    );
  }

  return (
    <>
      <div className="mt-2 flex items-center gap-2">
        <Segment on={span === 7} onClick={() => setSpan(7)}>
          This week
        </Segment>
        <Segment on={span === 14} onClick={() => setSpan(14)}>
          2 weeks
        </Segment>
      </div>

      <div className="mt-4">
        <Columns columns={result.columns} height={300} cap={14} numbers={result.numbers} />
      </div>

      {result.clusters.length ? (
        <ol className="m-0 mt-section flex list-none flex-col gap-3 p-0">
          {result.clusters.map((c) => (
            <li key={c.rule} className="rounded-panel bg-surface p-panel">
              <div className="flex items-center gap-3">
                <NumberBadge n={c.number} size={22} />
                <p className="type-body m-0 font-semibold text-ink">{c.title}</p>
              </div>
              <p className="type-secondary m-0 mt-1 text-muted">{c.days}</p>
              <p className="type-secondary m-0 mt-3 text-text tabular-nums">
                <span className="font-semibold text-ink">Body. </span>
                {c.body}
              </p>
              <p className="type-secondary m-0 mt-2 text-text tabular-nums">
                <span className="font-semibold text-ink">Mind. </span>
                {c.mind}
              </p>
            </li>
          ))}
        </ol>
      ) : (
        <p className="type-body m-0 mt-section text-text">Nothing outside the protocol in these days.</p>
      )}

      <section aria-label="Summary" className="mt-section">
        <p className="type-body m-0 font-semibold text-ink tabular-nums">{result.summary}</p>
        {result.lever ? <p className="type-body m-0 mt-1 text-text">{result.lever}</p> : null}
        {result.notes.map((note) => (
          <p key={note} className="type-secondary m-0 mt-3 text-text tabular-nums">
            {note}
          </p>
        ))}
        <p className="type-secondary m-0 mt-3 text-muted tabular-nums">{result.month}</p>
      </section>
    </>
  );
}
