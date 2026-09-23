"use client";

import { Chip, Sheet } from "@/components/ui";
import { RULES, type Rule, type RuleId } from "@/lib/rules";
import { SOURCES, sourceLine } from "@/lib/sources";

/** "Measured", "Assumed", "Protocol default". */
function claim(r: Rule): string {
  return r.claim === "measured" ? "Measured" : r.claim === "assumed" ? "Assumed" : "Protocol default";
}

/** "Costs about 3% of cognition and 1% of body; about 45 min of sleep that night." */
function size(r: Rule): string {
  const parts: string[] = [];
  if (r.effect.cognition) parts.push(`${Math.round(r.effect.cognition * 1000) / 10}% of cognition`);
  if (r.effect.body) parts.push(`${Math.round(r.effect.body * 1000) / 10}% of body`);
  const sleep = r.sleepMinutes ? `; about ${r.sleepMinutes} min of sleep that night` : "";
  return parts.length ? `Costs about ${parts.join(" and ")}${sleep}.` : `Drawn, not scored${sleep}.`;
}

/**
 * The biggest lever's rule on a sheet: its name, window, what breaking it
 * does, its grade and claim, and the studies behind it.
 */
export function LeverSheet({ rule, open, onClose }: { rule: RuleId | null; open: boolean; onClose: () => void }) {
  const r = rule ? RULES[rule] : null;
  return (
    <Sheet open={open && r !== null} onClose={onClose} title={r?.name ?? "Biggest lever"} id="lever">
      {r ? (
        <>
          <p className="type-secondary m-0 mt-2 text-center text-muted">{r.window}</p>
          <p className="type-body m-0 mt-4 text-text">{r.consequence}</p>
          <p className="type-body m-0 mt-3 font-semibold text-ink">{r.lever}.</p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Chip tone={r.grade === "A" ? "good" : "neutral"}>Grade {r.grade}</Chip>
            <Chip>{claim(r)}</Chip>
          </div>
          <p className="type-caption m-0 mt-3 text-muted tabular-nums">{size(r)}</p>
          {r.assumed ? <p className="type-caption m-0 mt-1 text-muted">{r.assumed}.</p> : null}
          {r.sources.length ? (
            <ul className="m-0 mt-4 list-none p-0" aria-label="Sources">
              {r.sources.map((key) => (
                <li key={key} className="hairline py-3">
                  <p className="type-secondary m-0 text-ink">{sourceLine(key)}</p>
                  {SOURCES[key] ? <p className="type-caption m-0 mt-1 text-muted">{SOURCES[key].finding}</p> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="type-caption m-0 mt-4 text-muted">A protocol default: no study sets this number.</p>
          )}
        </>
      ) : null}
    </Sheet>
  );
}
