"use client";

import { Chip, InsetList, ListRow, Sheet } from "@/components/ui";
import { RULES, type Rule } from "@/lib/rules";
import { sourceLine } from "@/lib/sources";

const ORDER: Rule["id"][] = [
  "caffeine", "sleep_debt", "alcohol", "exercise_timing", "last_meal", "screens", "phone_in_bed", "co2",
  "sleep_regularity", "social_jetlag", "morning_light", "nature", "conversation", "hydration", "nap", "sauna",
  "postpartum", "skipped_meal", "eating_window", "food_quality", "sedentary", "stress", "air", "uv", "peptide",
  "sleep_deep", "wake_anchor", "sick",
];

function when(rule: Rule): string {
  const day = rule.lands === 0 ? "the same day" : "the next day";
  const fade =
    rule.decayDays <= 0 ? "that day only" : rule.decayDays === 1 ? "gone a day later" : `fades over ${rule.decayDays} days`;
  return `Lands ${day}, ${fade}.`;
}

function size(rule: Rule): string {
  const parts: string[] = [];
  if (rule.effect.cognition) parts.push(`about ${Math.round(rule.effect.cognition * 1000) / 10}% of cognition`);
  if (rule.effect.body) parts.push(`about ${Math.round(rule.effect.body * 1000) / 10}% of body`);
  const sleep = rule.sleepMinutes ? ` About ${rule.sleepMinutes} min of sleep that night.` : "";
  return parts.length ? `Costs ${parts.join(" and ")}.${sleep}` : `Drawn, not scored.${sleep}`;
}

function claim(rule: Rule): string {
  if (rule.claim === "measured") return "measured";
  if (rule.claim === "assumed") return "assumed";
  return "protocol default";
}

/**
 * Every rule in plain words, one inset group each: the name with its grade chip
 * and the window as the first row; then what breaking it does, how much it costs
 * each ceiling, when it lands and how fast it fades, and the studies behind it.
 */
export function HowSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Sheet open={open} onClose={onClose} title="How it’s computed" id="how">
      <p className="type-secondary m-0 mt-3 text-text">
        Cognition and Body are the percent of your ceiling you used today. Each rule below costs a share of a ceiling
        when it is broken. Costs multiply across the day, carry into the days after while they fade, and a clean
        streak brings both back to 100.
      </p>
      <p className="type-caption m-0 mt-2 text-muted">
        Grade A: a meta-analysis or a large trial. B: a controlled study or a large cohort. C: a protocol default with no
        study behind the number. Measured: a study gives the number. Assumed: we scaled it.
      </p>
      <div className="mt-4 flex flex-col gap-3">
        {ORDER.map((id) => {
          const rule = RULES[id];
          return (
            <InsetList key={id}>
              <ListRow title={rule.name} detail={rule.window} trailing={<Chip>Grade {rule.grade}</Chip>} />
              <li className="relative px-4 py-3 before:absolute before:top-0 before:right-0 before:left-4 before:hairline">
                <p className="type-secondary m-0 text-text">{rule.consequence}</p>
                <p className="type-caption m-0 mt-2 text-muted tabular-nums">
                  {size(rule)} {when(rule)} Grade {rule.grade}, {claim(rule)}.
                </p>
                {rule.assumed ? <p className="type-caption m-0 mt-1 text-muted">{rule.assumed}.</p> : null}
                {rule.sources.map((key) => (
                  <p key={key} className="type-caption m-0 mt-1 text-muted">
                    {sourceLine(key)}
                  </p>
                ))}
              </li>
            </InsetList>
          );
        })}
      </div>
    </Sheet>
  );
}
