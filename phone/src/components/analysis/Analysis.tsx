"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Columns } from "@/components/calendar/Columns";
import { Button, EmptyState, ErrorState, LoadingState, SegmentedControl } from "@/components/ui";
import { analyze } from "@/lib/analysis";
import { insightsFor } from "@/lib/insights";
import { useMonth } from "@/lib/useMonth";
import { ClusterCard } from "./ClusterCard";
import { InsightCard } from "./InsightCard";
import { LeverSheet } from "./LeverSheet";

type Span = "7" | "14" | "30";

const SPANS = [
  { id: "7", label: "This week" },
  { id: "14", label: "2 weeks" },
  { id: "30", label: "30 days" },
] as const satisfies readonly { id: Span; label: string }[];

const RANGE_LABEL: Record<Span, string> = { "7": "This week", "14": "These 2 weeks", "30": "These 30 days" };

/** The columns: the week view's strip at 32 wide, the night cap 14 px of it. */
const COLUMN_H = 300;
const COLUMN_CAP = 14;

/**
 * Analysis: the days side by side with a number on every red bar (one number
 * per rule, so clusters show at a glance), one card per number with what it
 * did to the body and to the mind, the totals with the one lever, then the
 * insight cards the month supports. No lines between things; the numbers do
 * the pointing.
 */
export function Analysis() {
  const month = useMonth();
  const [span, setSpan] = useState<Span>("7");
  const [leverOpen, setLeverOpen] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  const days = useMemo(() => month.month?.days ?? [], [month.month]);
  const range = useMemo(() => {
    const last = days.length - 1;
    return { from: Math.max(0, last - Number(span) + 1), to: last };
  }, [days.length, span]);
  const result = useMemo(() => {
    if (!days.length) return null;
    return analyze(days, month.findings, month.operating, range.from, range.to, RANGE_LABEL[span]);
  }, [days, month.findings, month.operating, range, span]);
  const insights = useMemo(() => (days.length ? insightsFor(days, month.findings, month.operating, range.from, range.to) : []), [days, month.findings, month.operating, range]);

  // A longer range scrolls; it opens on the most recent days, the lead at the left.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [span, result?.columns.length]);

  if (!month.loaded) return <LoadingState line="Reading the month…" blocks={3} />;
  if (!result) {
    return month.error === "unreachable" ? (
      <ErrorState
        sentence="The Mac is not answering. The analysis appears once it does."
        action={
          <Button variant="secondary" onClick={() => window.location.reload()}>
            Try again
          </Button>
        }
      />
    ) : (
      <EmptyState text="The analysis appears once the backend serves a month. Fixtures mode shows a seeded month." />
    );
  }

  return (
    <>
      <div className="mt-2">
        <SegmentedControl options={SPANS} value={span} onChange={setSpan} size={32} ariaLabel="Range" />
      </div>

      {/* Bleeds to the screen edges with a 16 px lead; 14 and 30 columns scroll and snap, no scrollbar. */}
      <div
        ref={scroller}
        className="-mx-gutter mt-4 overflow-x-auto px-gutter [scrollbar-width:none] [&::-webkit-scrollbar]:hidden snap-x snap-mandatory scroll-pl-gutter [&_[role=group]]:snap-start"
      >
        <div className="w-max min-w-full">
          <Columns columns={result.columns} height={COLUMN_H} cap={COLUMN_CAP} numbers={result.numbers} width={32} />
        </div>
      </div>

      {result.clusters.length ? (
        <ol className="m-0 mt-section flex list-none flex-col gap-3 p-0" aria-label="What the reds cost">
          {result.clusters.map((c) => (
            <ClusterCard key={c.rule} cluster={c} />
          ))}
        </ol>
      ) : (
        <p className="type-body m-0 mt-section text-text">Nothing outside the protocol in these days.</p>
      )}

      <section aria-label="Totals" className="mt-section rounded-card bg-surface p-card">
        <p className="type-secondary m-0 text-muted">{RANGE_LABEL[span]}, average of ceiling</p>
        <div className="mt-2 grid grid-cols-2">
          <div>
            <p className="type-secondary m-0 text-muted">Cognition</p>
            <p className="type-number m-0 text-ink">{result.average.cognition}%</p>
          </div>
          <div className="border-l border-hairline pl-4">
            <p className="type-secondary m-0 text-muted">Body</p>
            <p className="type-number m-0 text-ink">{result.average.body}%</p>
          </div>
        </div>
        {result.notes.map((note) => (
          <p key={note} className="type-secondary m-0 mt-3 text-text tabular-nums">
            {note}
          </p>
        ))}
        <p className="type-caption m-0 mt-3 text-muted tabular-nums">{result.month}</p>
        {result.lever && result.leverRule ? (
          <div className="mt-2">
            <Button variant="tertiary" onClick={() => setLeverOpen(true)}>
              {result.lever}
            </Button>
          </div>
        ) : null}
      </section>

      {insights.length ? (
        <section aria-labelledby="insights-title" className="mt-section">
          <h2 id="insights-title" className="type-section m-0 text-ink">
            What the month says
          </h2>
          <ul className="m-0 mt-3 flex list-none flex-col gap-3 p-0">
            {insights.map((insight) => (
              <InsightCard key={insight.id} insight={insight} />
            ))}
          </ul>
        </section>
      ) : null}

      <LeverSheet rule={result.leverRule} open={leverOpen} onClose={() => setLeverOpen(false)} />
    </>
  );
}
