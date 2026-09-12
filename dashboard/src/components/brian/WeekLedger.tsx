import { T } from "@/lib/tokens";
import type { LedgerRow } from "@/lib/score/types";
import { fmtNum } from "./format";
import { H2, Panel, Track } from "./Panel";

/** Share of the target still missing at this pace; biggest gap first. */
const gap = (l: LedgerRow): number => (l.target > 0 ? (l.target - l.projected) / l.target : 0);

export function WeekLedger({ ledger }: { ledger: LedgerRow[] }) {
  const open = [...ledger].sort((a, b) => gap(b) - gap(a));
  return (
    <Panel id="week" labelledBy="week-title">
      <H2 id="week-title" sub="Accrued so far, and where you land by Sunday at this pace.">
        This week
      </H2>
      {open.length === 0 ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          Nothing accrued yet this week.
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-4 p-0">
          {open.map((l) => {
            const behind = l.status !== "on_track";
            return (
              <li key={l.key}>
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <div className="text-sm font-semibold" style={{ color: T.ink }}>
                    {l.label}
                  </div>
                  <div className="tnum text-sm" style={{ color: behind ? T.cost : T.muted }}>
                    {fmtNum(l.accrued)} of {fmtNum(l.target)} {l.unit} · on pace for {fmtNum(l.projected)}
                  </div>
                </div>
                <div className="mt-2">
                  <Track
                    value={l.projected}
                    max={l.target}
                    color={behind ? T.cost : T.earn}
                    label={`${l.label}: on pace for ${fmtNum(l.projected)} of ${fmtNum(l.target)} ${l.unit}`}
                  />
                </div>
                {behind && (
                  <div className="tnum mt-1 text-sm" style={{ color: T.muted }}>
                    {fmtNum(l.deficit)} {l.unit} still needed.
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
