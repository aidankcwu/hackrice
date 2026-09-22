import type { Outcome } from "@/lib/today";
import { OUTCOME_ICONS } from "./icons";

/** Trailing outcome: symbol + word, no capsule. "held back" is muted words, no symbol. */
export function OutcomeLabel({ outcome }: { outcome: Outcome }) {
  if (outcome === "held back") {
    return <span className="type-outcome whitespace-nowrap font-normal text-muted">held back</span>;
  }
  const Icon = OUTCOME_ICONS[outcome];
  return (
    <span className="type-outcome inline-flex items-center gap-1 whitespace-nowrap text-ink">
      <Icon className="size-[calc(14px*var(--type-scale))] shrink-0" strokeWidth={2} aria-hidden="true" />
      {outcome}
    </span>
  );
}
