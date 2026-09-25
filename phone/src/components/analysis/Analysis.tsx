"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Columns, ColumnsCaption, HourAxis } from "@/components/calendar/Columns";
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
/** A 32 px column and the 8 px gap after it. */
const COLUMN_GAP = 8;
const COLUMN_PITCH = 32 + COLUMN_GAP;

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

      {/* The hours stay pinned; the days scroll beside them, 14 and 30 snapping a column at a time, no
          scrollbar. The scroller is a whole number of 40 px columns wide (32 + the 8 px gap, less the
          last gap), so it opens on the latest day with the leftmost visible column whole, never cut.
          The caption sits outside the scroller so it wraps to the screen instead of widening the row. */}
      <div className="mt-4 flex gap-2">
        <HourAxis height={COLUMN_H} cap={COLUMN_CAP} />
        <div className="min-w-0 flex-1">
          <div
            ref={scroller}
            data-chart
            className="overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden snap-x snap-mandatory [&_[role=group]]:snap-start"
            style={{ width: `calc(round(down, 100% + ${COLUMN_GAP}px, ${COLUMN_PITCH}px) - ${COLUMN_GAP}px)` }}
          >
            <div className="w-max">
              <Columns columns={result.columns} height={COLUMN_H} cap={COLUMN_CAP} numbers={result.numbers} width={32} caption={false} hours={false} />
            </div>
          </div>
        </div>
      </div>
      <ColumnsCaption sleep={result.columns.some((c) => c.sleepMinutes !== undefined)} />

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
