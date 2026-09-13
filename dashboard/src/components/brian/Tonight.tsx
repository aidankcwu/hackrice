import { T } from "@/lib/tokens";
import type { ForecastView } from "@/lib/score/types";
import { fmtSigned } from "./format";
import { H2, Panel } from "./Panel";

export function Tonight({ f }: { f: ForecastView }) {
  const tiles: ReadonlyArray<[string, string]> = [
    ["Sleep", `${f.sleep_hours.toFixed(1)} h`],
    ["HRV", fmtSigned(f.hrv_change_pct, 0, "%")],
    ["Clock", fmtSigned(f.melatonin_delay_min, 0, " min")],
  ];
  return (
    <Panel labelledBy="tonight-title">
      <H2 id="tonight-title" sub={`Habitual bedtime ${f.bedtime}.`}>
        Tonight, if nothing changes
      </H2>
      <dl className="m-0 grid grid-cols-3 gap-3">
        {tiles.map(([k, v]) => (
          <div key={k} className="rounded-2xl p-3 sm:p-4" style={{ background: T.bg }}>
            <dt className="text-xs font-medium" style={{ color: T.muted }}>
              {k}
            </dt>
            <dd className="tnum m-0 mt-1 text-2xl font-bold" style={{ color: T.ink }}>
              {v}
            </dd>
          </div>
        ))}
      </dl>
      {f.drivers.length > 0 && (
        <p className="m-0 mt-4 text-sm" style={{ color: T.text }}>
          Because: {f.drivers.join("; ")}.
        </p>
      )}
      <p className="m-0 mt-2 text-sm font-medium" style={{ color: T.ink }}>
        {f.fix}
      </p>
    </Panel>
  );
}
