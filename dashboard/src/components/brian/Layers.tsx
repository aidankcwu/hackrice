import { T, fmtH, tone } from "@/lib/tokens";
import type { LayerRow } from "@/lib/score/types";
import { Icon } from "./icons";
import { H2, Panel, Track } from "./Panel";

export function Layers({ layers }: { layers: LayerRow[] }) {
  return (
    <Panel labelledBy="layers-title" className="md:col-span-7">
      <H2 id="layers-title" sub="Score per layer, and what each earned or cost today.">
        Where it came from
      </H2>
      {layers.length === 0 ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          No layers scored yet.
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {layers.map((l) => {
            const score = Math.round(l.score);
            const unmeasured = l.total - l.measured;
            return (
              <li
                key={l.name}
                className="tile grid grid-cols-12 items-center gap-x-3 gap-y-2 rounded-2xl px-3 py-3"
                style={{ background: T.bg }}
              >
                {/* Phones: name + hours on one line, track + score below. md and up: the reference's single row. */}
                <div className="order-1 col-span-8 flex min-w-0 items-center gap-3 md:col-span-5">
                  <span
                    className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full"
                    style={{ background: T.surface }}
                  >
                    <Icon name={l.icon} color={T.ink} />
                  </span>
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold" style={{ color: T.ink }}>
                      {l.name}
                    </div>
                    <div className="truncate text-sm" style={{ color: T.muted }}>
                      {l.note}
                      {unmeasured > 0 && ` · ${unmeasured} of ${l.total} unmeasured`}
                    </div>
                  </div>
                </div>
                <div className="order-3 col-span-9 md:order-2 md:col-span-4">
                  <Track value={score} color={score < 40 ? T.cost : T.ink} label={`${l.name} score`} />
                </div>
                <div className="tnum order-4 col-span-3 text-right text-sm font-bold md:order-3 md:col-span-1" style={{ color: T.ink }}>
                  {score}
                </div>
                <div
                  className="tnum order-2 col-span-4 whitespace-nowrap text-right text-sm font-bold md:order-4 md:col-span-2"
                  style={{ color: tone(l.hours) }}
                >
                  {Math.abs(l.hours) < 0.05 ? "—" : fmtH(l.hours)}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
