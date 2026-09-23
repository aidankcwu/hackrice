"use client";

import { Sheet } from "@/components/Sheet";
import { RULES, type Rule } from "@/lib/rules";

const ORDER: Rule["id"][] = [
  "caffeine", "last_meal", "eating_window", "skipped_meal", "food_quality", "alcohol", "nicotine",
  "movement", "sedentary", "nap", "screens", "phone_in_bed", "sleep_window", "sleep_short",
  "sleep_deep", "sleep_fragmented", "wake_anchor", "morning_light", "daylight", "people", "water",
  "peptide", "sauna_cold", "stress", "air", "sick",
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
  return parts.length ? `Costs ${parts.join(" and ")}.` : "Drawn, not scored: the cost shows up in the night that follows.";
}

/**
 * Every rule in plain words: the window, what breaking it does, how much it
 * costs each ceiling, when it lands and how fast it fades. Every number is a
 * working estimate until its source is checked.
 */
export function HowSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Sheet open={open} onClose={onClose} title="How it’s computed" id="how">
      <p className="type-secondary m-0 mt-2 text-text">
        Cognition and Body are the percent of your ceiling you used today. Each rule below costs a share of a ceiling
        when it is broken. Costs multiply across the day, carry into the days after while they fade, and a clean
        streak brings both back to 100.
      </p>
      <p className="type-caption m-0 mt-2 text-muted">Every number here is an estimate. Sources are marked to verify.</p>
      <ul className="m-0 mt-4 list-none p-0">
        {ORDER.map((id) => {
          const rule = RULES[id];
          return (
            <li key={id} className="border-t-[0.5px] border-line py-4">
              <p className="type-body m-0 font-semibold text-ink">{rule.name}</p>
              <p className="type-secondary m-0 mt-1 text-muted">{rule.window}</p>
              <p className="type-secondary m-0 mt-2 text-text">{rule.consequence}</p>
              <p className="type-caption m-0 mt-2 text-muted tabular-nums">
                {size(rule)} {when(rule)} Source: {rule.source}.
              </p>
            </li>
          );
        })}
      </ul>
    </Sheet>
  );
}
