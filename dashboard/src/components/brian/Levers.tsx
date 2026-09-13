import { T, fmtH } from "@/lib/tokens";
import { LAYER_LABELS } from "@/lib/score/units";
import type { LeverRow } from "@/lib/score/types";
import { H2, Panel } from "./Panel";

const layerNames = (keys: string[]): string => keys.map((k) => LAYER_LABELS[k] ?? k).join(", ");

export function Levers({ levers }: { levers: LeverRow[] }) {
  return (
    <Panel id="plan" labelledBy="levers-title">
      <H2 id="levers-title" sub="Ranked by healthy-life hours gained per minute it takes.">
        Best use of your next minutes
      </H2>
      {levers.length === 0 ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          No levers yet. They appear once today has measured doses to improve on.
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {levers.map((l) => (
            <li
              key={l.key}
              className="tile flex items-center justify-between gap-4 rounded-2xl px-4 py-3"
              style={{ background: T.bg }}
            >
              <div className="min-w-0">
                <div className="text-sm font-semibold" style={{ color: T.ink }}>
                  {l.action}
                </div>
                <div className="text-sm" style={{ color: T.muted }}>
                  {l.time === 0 ? "Costs no time" : `${l.time} minutes`}
                  {l.layers.length > 0 && ` · ${layerNames(l.layers)}`}
                </div>
              </div>
              <div className="tnum shrink-0 text-base font-bold" style={{ color: T.earn }}>
                {fmtH(l.gain)}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
