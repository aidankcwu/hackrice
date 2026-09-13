import { T, fmtH, tone } from "@/lib/tokens";
import type { DashboardData } from "@/lib/score/types";
import { fmtSigned } from "./format";
import { Panel } from "./Panel";

export interface TodayProps {
  d: DashboardData;
  /** A fresh payload is on its way; shown quietly on the label line so nothing moves. */
  updating?: boolean;
}

export function Today({ d, updating = false }: TodayProps) {
  const lost = d.hours_today < -0.05;
  return (
    <Panel id="today" labelledBy="today-title" className="md:col-span-5">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="today-title" className="m-0 text-sm font-medium" style={{ color: T.muted }}>
          Today
        </h2>
        <span className="text-sm" style={{ color: T.muted }} aria-live="polite">
          {updating ? "Updating…" : ""}
        </span>
      </div>
      <p
        className="tnum m-0 mt-2 font-extrabold leading-none tracking-tight"
        style={{ fontSize: 72, color: tone(d.hours_today) }}
      >
        {fmtH(d.hours_today)}
      </p>
      <p className="m-0 mt-3 text-base" style={{ color: T.text }}>
        of healthy life {lost ? "lost" : "earned"} today, from everything {d.person.device} saw. Likely range{" "}
        {fmtH(d.hours_ci[0])} to {fmtH(d.hours_ci[1])}.
      </p>
      <div
        className="mt-6 flex flex-wrap items-start justify-between gap-4 pt-5"
        style={{ borderTop: `1px solid ${T.line}` }}
      >
        <div>
          <div className="text-sm" style={{ color: T.muted }}>
            Trajectory if this is a normal day
          </div>
          <div className="tnum text-2xl font-bold" style={{ color: T.ink }}>
            {fmtSigned(d.years_delta, 1)} years
          </div>
          <div className="tnum text-sm" style={{ color: T.muted }}>
            vs a typical person your age · {fmtSigned(d.years_ci[0], 1)} to {fmtSigned(d.years_ci[1], 1)}
          </div>
        </div>
        <div className="text-right">
          <div className="text-sm" style={{ color: T.muted }}>
            Healthspan score
          </div>
          <div className="tnum text-2xl font-bold" style={{ color: T.ink }}>
            {Math.round(d.overall)}
            <span className="text-base font-medium" style={{ color: T.muted }}>
              /100
            </span>
          </div>
        </div>
      </div>
    </Panel>
  );
}
