"use client";

import { Button } from "@/components/ui";
import { RESULT_MEDIAN_LABEL } from "@/content/tests";
import type { TestDef } from "@/content/types";
import { dayStrip30, formatScore, history, median7, restsOnSeeded, useTestStore, type TestResult } from "@/lib/tests";
import { TestFrame } from "./TestFrame";

export interface ResultsProps {
  test: TestDef;
  /** The run just saved. */
  entry: TestResult;
  theme?: "light" | "dark";
  scale?: number;
}

/** The score's label, sentence case except d′. */
const SCORE_LABEL: Record<TestDef["id"], string> = {
  pvt: "Mean reaction time",
  nback: "d′",
  dsst: "Correct",
  stroop: "Interference",
};

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The extra metrics as one line, in the order the content lists them. */
function extrasLine(id: TestDef["id"], extra: Record<string, number>): string {
  const n = (key: string) => extra[key] ?? 0;
  switch (id) {
    case "pvt":
      return `Mean RT ${n("meanRt")} ms · ${count(n("lapses"), "lapse", "lapses")} over 355 ms · ${count(n("falseStarts"), "false start", "false starts")} · ${count(n("trials"), "trial", "trials")}`;
    case "nback":
      return `${count(n("hits"), "hit", "hits")} · ${count(n("misses"), "miss", "misses")} · ${count(n("falseAlarms"), "false alarm", "false alarms")} · ${count(n("correctRejections"), "correct rejection", "correct rejections")}`;
    case "dsst":
      return `${n("correct")} correct · ${n("wrong")} wrong · ${count(n("trials"), "trial", "trials")}`;
    case "stroop":
      return `Congruent ${n("congruentRt")} ms · incongruent ${n("incongruentRt")} ms · ${count(n("errors"), "error", "errors")} · ${count(n("trials"), "trial", "trials")}`;
  }
}

function withUnit(id: TestDef["id"], unit: string, value: number | null): string {
  if (value === null) return "none yet";
  return unit ? `${formatScore(id, value)} ${unit}` : formatScore(id, value);
}

/**
 * The result screen after a run: the score at 56 with its unit, the 7-day
 * median (marked "seeded" while it rests on placeholder days), a 30-day strip
 * of 8 px cells, the extra metrics, and "Done". History comes from the store
 * hook, so nothing reads storage during render.
 */
export function Results({ test, entry, theme, scale }: ResultsProps) {
  const { store, loaded } = useTestStore();
  const median = loaded ? median7(test.id, store) : null;
  const seeded = loaded && restsOnSeeded(test.id, store);
  const strip = loaded ? dayStrip30(test.id, store) : Array.from({ length: 30 }, () => false);
  const daysWithRuns = strip.filter(Boolean).length;
  // Seeded days light the strip like real runs, so the label and caption say how many are seeded.
  const seededDays = loaded ? history(test.id, store).filter((entry) => entry.seeded).length : 0;
  const seededNote = seededDays ? `, ${seededDays} seeded` : "";

  return (
    <TestFrame line={test.name} theme={theme} scale={scale}>
      <div className="flex flex-1 flex-col pt-4">
        <p className="type-secondary m-0 text-muted">{SCORE_LABEL[test.id]}</p>
        <p className="type-hero m-0 text-ink">
          {formatScore(test.id, entry.score)}
          {test.metricUnit ? <span className="type-section ml-2 font-semibold text-muted">{test.metricUnit}</span> : null}
        </p>
        <p className="type-secondary m-0 mt-2 text-muted">
          {RESULT_MEDIAN_LABEL}: {loaded ? withUnit(test.id, test.metricUnit, median) : "…"}
          {seeded ? <span className="type-caption ml-2">seeded</span> : null}
        </p>

        <div
          role="img"
          aria-label={`Runs on ${daysWithRuns} of the last 30 days${seededNote}`}
          className="mt-section flex gap-0.5"
        >
          {strip.map((has, i) => (
            <span
              key={i}
              className={`size-2 shrink-0 rounded-xs ${i === strip.length - 1 ? "bg-ink" : has ? "bg-good" : "bg-band"}`}
            />
          ))}
        </div>
        <p className="type-caption m-0 mt-2 text-muted tabular-nums">Last 30 days{seededNote}, today at the right</p>

        <p className="type-caption m-0 mt-4 text-muted">{extrasLine(test.id, entry.extra)}</p>

        <div className="mt-auto pt-6 pb-4">
          <Button href="/tests">Done</Button>
        </div>
      </div>
    </TestFrame>
  );
}
