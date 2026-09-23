import { Chip } from "@/components/ui";
import { hoursWord, provenanceChip, signed, type Tone } from "@/lib/today";
import type { Healthspan } from "@/lib/types";

/** Colour sits on the number only, and only next to its sign and word. */
const TONE: Record<Tone, string> = { earn: "text-good", cost: "text-bad", zero: "text-ink" };

/**
 * The one panel on Today: `hours_today` as the hero number in its sign colour,
 * its word 4 px below with the provenance chip at the right of that line, then
 * the score line.
 */
export function Hero({ healthspan }: { healthspan: Healthspan }) {
  const hours = signed(healthspan.hours_today, "h", "hours");
  // "healthy" is said once, on the word line; the score line reads "+0.3 years at this pace".
  const years = signed(healthspan.years_delta ?? 0, "years at this pace");
  const chip = provenanceChip(healthspan);
  return (
    <section aria-label="Healthy life today" className="rounded-card bg-surface p-card">
      <p className={`type-hero m-0 ${TONE[hours.tone]}`}>
        <span aria-hidden="true">{hours.text}</span>
        <span className="sr-only">{hours.spoken}</span>
      </p>
      <div className="mt-1 flex items-center justify-between gap-3">
        <span className="type-secondary min-w-0 text-muted">{hoursWord(healthspan.hours_today)}</span>
        {chip ? <Chip>{chip}</Chip> : null}
      </div>
      <p className="type-secondary m-0 mt-3 text-text tabular-nums">
        Score {Math.round(healthspan.overall)} · <span aria-hidden="true">{years.text}</span>
        <span className="sr-only">{years.spoken}</span>
      </p>
    </section>
  );
}
