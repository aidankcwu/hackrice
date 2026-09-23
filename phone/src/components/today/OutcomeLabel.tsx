import { Chip } from "@/components/ui";
import { sentence, type Outcome } from "@/lib/today";
import { OUTCOME_ICONS } from "./icons";

/** The outcome as a chip: icon and word. "Held back" is the same chip, quiet by its word. */
export function OutcomeLabel({ outcome }: { outcome: Outcome }) {
  return <Chip icon={OUTCOME_ICONS[outcome]}>{sentence(outcome)}</Chip>;
}
