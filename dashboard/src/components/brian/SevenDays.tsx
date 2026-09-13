"use client";
import { Bar, BarChart, Rectangle, ReferenceLine, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import type { BarShapeProps } from "recharts";
import { T, fmtH, tone } from "@/lib/tokens";
import type { WeekDay } from "@/lib/score/types";
import { fmtNum, orDash } from "./format";
import { H2, Panel } from "./Panel";

const RADIUS = 4;

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

function chartLabel(week: WeekDay[]): string {
  const scored = week.filter(isScored);
  if (scored.length === 0) return "Healthy-life hours per day: no days scored yet.";
  const best = scored.reduce((a, b) => (b.hours > a.hours ? b : a));
  const worst = scored.reduce((a, b) => (b.hours < a.hours ? b : a));
  return `Healthy-life hours per day for the last seven days. Best ${best.day} ${fmtH(best.hours)}, worst ${worst.day} ${fmtH(worst.hours)}.`;
}

export function SevenDays({ week, summary }: { week: WeekDay[]; summary: string }) {
  const chartData = week.map((r) => ({ day: r.day, hours: r.hours }));
  return (
    <Panel labelledBy="seven-title">
      <H2 id="seven-title" sub="Healthy-life hours per day, with the WHOOP numbers behind them.">
        Last seven days
      </H2>
      {week.length === 0 ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          No days scored yet.
        </p>
      ) : (
        <>
          <div className="h-32" role="img" aria-label={chartLabel(week)}>
            <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 600, height: 128 }}>
              <BarChart data={chartData} margin={{ top: 8, right: 0, bottom: 0, left: 0 }} barCategoryGap="15%">
                <XAxis dataKey="day" tick={{ fontSize: 12, fill: T.muted }} axisLine={false} tickLine={false} />
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

          <div className="mt-4">
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
                {week.map((r) => (
                  <tr
                    key={r.date}
                    className={r.today ? "font-semibold" : undefined}
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
                      {r.tag}
                    </td>
                  </tr>
                ))}
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
