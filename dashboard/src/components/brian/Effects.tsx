import { T } from "@/lib/tokens";
import type { EffectRow } from "@/lib/score/types";
import { H2, Panel } from "./Panel";

const isNum = (v: number | null): v is number => v !== null;

/**
 * CI bars share one scale: the largest |bound| across rows sits at the edge
 * (±45% from the centre line), so an effect of 0.02 and one of 2.0 are both
 * legible instead of assuming the bounds fit in ±1.
 */
function ciScale(effects: EffectRow[]): number {
  const bounds = effects.flatMap((e) => [e.lo, e.hi]).filter(isNum).map(Math.abs);
  const max = bounds.length ? Math.max(...bounds) : 0;
  return max > 0 ? max : 1;
}

export function Effects({ effects }: { effects: EffectRow[] }) {
  const scale = ciScale(effects);
  return (
    <Panel labelledBy="effects-title">
      <H2 id="effects-title" sub="Estimated on your own days, not the population's. Greyed until 14 days of data.">
        What actually moves you
      </H2>
      {effects.length === 0 ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          No personal estimates yet. They start after 14 days of glasses and wearable data.
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-3 p-0">
          {effects.map((e) => {
            const { lo, hi } = e;
            const estimable = lo !== null && hi !== null;
            const left = estimable ? 50 + (lo / scale) * 45 : 50;
            const right = estimable ? 50 + (hi / scale) * 45 : 50;
            const colour = estimable ? (hi < 0 ? T.cost : lo > 0 ? T.earn : T.muted) : T.muted;
            const settled = e.ok && estimable;
            // "Greyed until 14 days": the reference dimmed the whole row, which
            // takes its text under 4.5:1. Muted text (5.1:1 on the panel) plus a
            // faded bar reads the same and stays legible.
            return (
              <li key={e.exposure + e.outcome} className="grid grid-cols-12 items-center gap-x-3 gap-y-1">
                <div className="col-span-12 text-sm font-medium md:col-span-5" style={{ color: settled ? T.ink : T.muted }}>
                  {e.exposure} <span style={{ color: T.muted }}>→</span> {e.outcome}
                </div>
                <div className="col-span-7 md:col-span-4" aria-hidden="true" style={{ opacity: settled ? 1 : 0.45 }}>
                  {estimable ? (
                    <div className="relative h-2 rounded-full" style={{ background: T.surface2 }}>
                      <div
                        className="absolute h-2 rounded-full"
                        style={{ left: `${left}%`, width: `${Math.max(0, right - left)}%`, background: colour }}
                      />
                      <div className="absolute top-0 h-2 w-px" style={{ left: "50%", background: T.ink }} />
                    </div>
                  ) : (
                    <div className="text-center text-sm" style={{ color: T.muted }}>
                      —
                    </div>
                  )}
                </div>
                <div className="col-span-5 text-right text-sm md:col-span-3" style={{ color: T.muted }}>
                  <span className="tnum">
                    {e.beta} · {e.n} days
                  </span>
                  {e.note && <span className="block">{e.note}</span>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
