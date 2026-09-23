"use client";
import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, Rectangle, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import type { BarShapeProps } from "recharts";
import { appFetch } from "@/lib/runtime";
import { T, fmtH, tone } from "@/lib/tokens";
import {
  annotationsFromWeek,
  driverLabel,
  templateSentence,
  type Annotation,
  type ContrastAnnotation,
  type NarratedSentence,
  type RunAnnotation,
  type WeekRow,
} from "@/lib/narrator";
import type { WeekDay } from "@/lib/score/types";
import { fmtNum, orDash } from "./format";
import { H2, Panel } from "./Panel";

/** screens.md §1.10: 6 px radius on the outer end of each bar. */
const RADIUS = 6;

/**
 * Rounds only the outer end of each bar. recharts hands a negative bar as
 * `y` at the value end with a negative `height`, so the rectangle is
 * normalised to top-left + positive height before choosing which corners
 * round; that keeps every bar flat against the zero baseline.
 */
function barShape(props: BarShapeProps) {
  const { x, y, width, height, value } = props;
  const signed = Array.isArray(value) ? value[1] - value[0] : value;
  const negative = signed < 0;
  return (
    <Rectangle
      x={x}
      y={Math.min(y, y + height)}
      width={width}
      height={Math.abs(height)}
      fill={negative ? T.cost : T.earn}
      radius={negative ? [0, 0, RADIUS, RADIUS] : [RADIUS, RADIUS, 0, 0]}
    />
  );
}

interface Column {
  key: string;
  label: string;
  /** Columns that are hidden below a breakpoint so the table never scrolls the page sideways. */
  className?: string;
  numeric: boolean;
  cell: (r: WeekDay) => string;
}

const COLUMNS: readonly Column[] = [
  { key: "bed", label: "Bed", className: "hidden md:table-cell", numeric: true, cell: (r) => r.bed },
  { key: "sleep", label: "Sleep", numeric: true, cell: (r) => orDash(r.sleep, (v) => `${v.toFixed(1)} h`) },
  { key: "hrv", label: "HRV", numeric: true, cell: (r) => orDash(r.hrv, (v) => `${v.toFixed(2)}×`) },
  { key: "rec", label: "Recovery", className: "hidden sm:table-cell", numeric: true, cell: (r) => orDash(r.rec, fmtNum) },
  { key: "rhr", label: "RHR", className: "hidden md:table-cell", numeric: true, cell: (r) => orDash(r.rhr, fmtNum) },
  { key: "steps", label: "Steps", numeric: true, cell: (r) => orDash(r.steps, fmtNum) },
  { key: "sri", label: "Regularity", className: "hidden md:table-cell", numeric: true, cell: (r) => orDash(r.sri, fmtNum) },
];

type ScoredDay = WeekDay & { hours: number };
const isScored = (r: WeekDay): r is ScoredDay => r.hours !== null;

function chartLabel(week: readonly WeekDay[]): string {
  const scored = week.filter(isScored);
  if (scored.length === 0) return "Healthy-life hours per day: no days scored yet.";
  const best = scored.reduce((a, b) => (b.hours > a.hours ? b : a));
  const worst = scored.reduce((a, b) => (b.hours < a.hours ? b : a));
  return `Healthy-life hours per day for the last seven days. Best ${best.day} ${fmtH(best.hours)}, worst ${worst.day} ${fmtH(worst.hours)}.`;
}

// ---------------------------------------------------------------------------
// The narrator layer
// ---------------------------------------------------------------------------

/**
 * `WeekDay.tag` carries the journal flags the adapter could prove — caffeine,
 * alcohol, nicotine, cannabis — so only those become drivers. The engine's own
 * `annotations[]` sees four more (night screens, late bed, no daylight,
 * isolation); when the payload supplies them they are used instead of these,
 * because a driver this function cannot see must not be counted as absent.
 */
function weekRows(week: readonly WeekDay[]): WeekRow[] {
  return week.filter(isScored).map((r) => ({
    day: r.day,
    hours: r.hours,
    sleep: r.sleep,
    rec: r.rec,
    drivers: {
      ...(r.tag.includes("caffeine") ? { caffeine_late: true } : {}),
      ...(r.tag.includes("alcohol") ? { alcohol: true } : {}),
    },
  }));
}

/** The drivers a day showed, in words: the `Seen` column (screens.md §1.10). */
function seenWords(row: WeekDay): string {
  const words = row.tag
    .split(",")
    .map((t) => t.trim())
    .filter((t) => t.length > 0)
    .map((t) => (t === "caffeine" ? "late caffeine" : t));
  return words.length > 0 ? words.join(", ") : "nothing flagged";
}

/**
 * One sentence per annotation. The templates render immediately, then
 * `POST /api/narrate` is asked to rephrase them; any sentence it returns
 * carrying a number that is not in its annotation is rejected server-side and
 * the template stands (see lib/narrator.ts). So the panel never waits on the
 * network and never shows an invented number.
 */
function useNarration(annotations: readonly Annotation[], days: readonly string[]): NarratedSentence[] {
  const ctx = useMemo(() => ({ days: [...days] }), [days]);
  const [sentences, setSentences] = useState<NarratedSentence[]>([]);

  useEffect(() => {
    let live = true;
    // Templates first, synchronously from the annotations themselves.
    setSentences(
      annotations.map((a) => ({
        kind: a.kind,
        text: templateSentence(a, ctx),
        origin: "template" as const,
        evidence: "evidence" in a ? a.evidence.filter((e) => e.length > 0) : [],
      })),
    );
    if (annotations.length === 0) return;
    void (async () => {
      try {
        const res = await appFetch("/api/narrate", {
          method: "POST",
          cache: "no-store",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ annotations, days: ctx.days }),
          signal: AbortSignal.timeout(8000),
        });
        if (!res.ok) return;
        const json: unknown = await res.json();
        const next = (json as { sentences?: unknown }).sentences;
        if (!live || !Array.isArray(next) || next.length !== annotations.length) return;
        setSentences(next as NarratedSentence[]);
      } catch {
        // The templates are already on screen, and they are the same facts.
        // A narrator that did not answer changes nothing the reader can see.
      }
    })();
    return () => {
      live = false;
    };
  }, [annotations, ctx]);

  return sentences;
}

const isRun = (a: Annotation): a is RunAnnotation => a.kind === "run";
const isContrast = (a: Annotation): a is ContrastAnnotation => a.kind === "contrast";

const num1 = (n: number): string => (Number.isInteger(n) ? String(n) : n.toFixed(1));

/**
 * A contrast as the two-column callout screens.md §1.10 specifies: the days
 * with the driver beside the days without, each with sleep, recovery and hours
 * per day, and the citation muted underneath.
 */
function ContrastCallout({ a }: { a: ContrastAnnotation }) {
  const columns: ReadonlyArray<{ head: string; sleep: number; rec: number; hours: number }> = [
    {
      head: `${a.n_with} ${driverLabel(a.driver)} day${a.n_with === 1 ? "" : "s"}`,
      sleep: a.sleep_with,
      rec: a.rec_with,
      hours: a.hours_with,
    },
    {
      head: `the other ${a.n_without}`,
      sleep: a.sleep_without,
      rec: a.rec_without,
      hours: a.hours_without,
    },
  ];
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {columns.map((c) => (
        <div key={c.head} className="p-4" style={{ background: T.bg, borderRadius: T.radiusPin }}>
          <div className="text-sm font-semibold" style={{ color: T.ink }}>
            {c.head}
          </div>
          <div className="tnum mt-1 text-sm" style={{ color: T.text }}>
            {num1(c.sleep)} h sleep · recovery {num1(c.rec)} ·{" "}
            <span style={{ color: tone(c.hours) }}>{fmtH(c.hours)}/day</span>
          </div>
        </div>
      ))}
      {a.evidence.length > 0 && (
        <p className="m-0 text-xs sm:col-span-2" style={{ color: T.muted }}>
          {a.evidence.join(" · ")}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

export interface SevenDaysProps {
  week: readonly WeekDay[];
  /** One data-derived sentence under the table. */
  summary: string;
  /**
   * `annotate_week` output from the engine payload. When absent the runs and
   * contrasts are derived here from the week rows, which see fewer drivers — so
   * the payload's own annotations are always preferred.
   */
  annotations?: readonly Annotation[];
}

export function SevenDays({ week, summary, annotations }: SevenDaysProps) {
  const chartData = week.map((r) => ({ day: r.day, hours: r.hours }));
  const days = useMemo(() => week.map((r) => r.day), [week]);
  const derived = useMemo(() => annotationsFromWeek(weekRows(week)), [week]);
  const facts = useMemo(() => (annotations && annotations.length > 0 ? [...annotations] : derived), [annotations, derived]);
  const sentences = useNarration(facts, days);
  const runs = facts.filter(isRun);
  const contrasts = facts.filter(isContrast);
  const worst = week.filter(isScored).reduce<ScoredDay | undefined>(
    (acc, r) => (acc === undefined || r.hours < acc.hours ? r : acc),
    undefined,
  );
  /** The sentence that belongs to an annotation, matched by position. */
  const sentenceFor = (a: Annotation): NarratedSentence | undefined => sentences[facts.indexOf(a)];

  return (
    <Panel id="seven" labelledBy="seven-title">
      <H2 id="seven-title" sub="Healthy-life hours per day, with the WHOOP numbers behind them.">
        Last seven days
      </H2>
      {week.length === 0 ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          No days scored yet.
        </p>
      ) : (
        <>
          <div className="h-36" role="img" aria-label={chartLabel(week)}>
            <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 600, height: 144 }}>
              <BarChart data={chartData} margin={{ top: 8, right: 0, bottom: 0, left: 0 }} barCategoryGap="15%">
                <XAxis dataKey="day" tick={{ fontSize: 12, fill: T.muted }} axisLine={false} tickLine={false} />
                {/* The narrator's soft band over each run, behind the bars. */}
                {runs.map((a) => (
                  <ReferenceArea
                    key={`${a.from}-${a.to}`}
                    x1={a.from}
                    x2={a.to}
                    fill={a.direction === 1 ? T.earnSoft : T.costSoft}
                    fillOpacity={1}
                    ifOverflow="extendDomain"
                  />
                ))}
                <ReferenceLine y={0} stroke={T.line} />
                <Tooltip
                  formatter={(value) => [fmtH(Number(value)), "healthy-life hours"]}
                  contentStyle={{ borderRadius: 12, borderColor: T.line, fontSize: 12, color: T.text }}
                  cursor={{ fill: T.surface2 }}
                />
                {/* Static bars: the page honours prefers-reduced-motion and a 1.5 s grow-in adds nothing to the reading. */}
                <Bar dataKey="hours" shape={barShape} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {runs.length > 0 && (
            <ul className="m-0 mt-3 flex list-none flex-col gap-1 p-0">
              {runs.map((a) => {
                const s = sentenceFor(a);
                return (
                  <li key={`run-${a.from}-${a.to}`} className="text-sm" style={{ color: T.text }}>
                    {s?.text ?? templateSentence(a, { days })}
                    {s && s.evidence.length > 0 && (
                      <span style={{ color: T.muted }}> {s.evidence.join(" · ")}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {contrasts.length > 0 && (
            <div className="mt-4 flex flex-col gap-4">
              {contrasts.map((a) => (
                <ContrastCallout key={`contrast-${a.driver}`} a={a} />
              ))}
            </div>
          )}

          <div className="mt-4 overflow-x-auto">
            <table className="w-full border-collapse text-sm" style={{ color: T.text }}>
              <thead>
                <tr style={{ color: T.muted }}>
                  <th scope="col" className="whitespace-nowrap pb-2 pr-3 text-left font-medium">
                    Day
                  </th>
                  {COLUMNS.map((c) => (
                    <th key={c.key} scope="col" className={`whitespace-nowrap pb-2 pr-3 text-left font-medium ${c.className ?? ""}`}>
                      {c.label}
                    </th>
                  ))}
                  <th scope="col" className="whitespace-nowrap pb-2 pr-3 text-left font-medium">
                    Hours
                  </th>
                  <th scope="col" className="hidden whitespace-nowrap pb-2 text-left font-medium sm:table-cell">
                    Seen
                  </th>
                </tr>
              </thead>
              <tbody>
                {week.map((r) => {
                  // The worst day is bold (screens.md §1.10); today keeps its marker.
                  const heavy = worst !== undefined && r.date === worst.date;
                  return (
                    <tr
                      key={r.date}
                      className={heavy ? "font-bold" : undefined}
                      aria-current={r.today ? "date" : undefined}
                      style={{ borderTop: `1px solid ${T.line}` }}
                    >
                      <th scope="row" className="py-2.5 pr-3 text-left font-semibold" style={{ color: T.ink }}>
                        {r.day}
                      </th>
                      {COLUMNS.map((c) => (
                        <td key={c.key} className={`${c.numeric ? "tnum " : ""}pr-3 ${c.className ?? ""}`}>
                          {c.cell(r)}
                        </td>
                      ))}
                      <td className="tnum whitespace-nowrap pr-3 font-bold" style={{ color: r.hours === null ? T.muted : tone(r.hours) }}>
                        {orDash(r.hours, fmtH)}
                      </td>
                      <td className="hidden break-words sm:table-cell" style={{ color: T.muted }}>
                        {seenWords(r)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      {summary && (
        <p className="m-0 mt-4 text-sm font-medium" style={{ color: T.ink }}>
          {summary}
        </p>
      )}
    </Panel>
  );
}

export default SevenDays;
