import { hoursWord, provenanceChip, signed, type Tone } from "@/lib/today";
import type { Healthspan } from "@/lib/types";

const TONE: Record<Tone, string> = { earn: "text-earn", cost: "text-cost", zero: "text-ink" };

/**
 * The one panel on Today: `hours_today` as the hero number, its word, the
 * provenance chip, and the score line. Colour sits on the number only.
 */
export function Hero({ healthspan }: { healthspan: Healthspan }) {
  const hours = signed(healthspan.hours_today, "h", "hours");
  // "healthy" is said once, on the first line; the score line reads "+0.3 years at this pace".
  const years = signed(healthspan.years_delta ?? 0, "years at this pace");
  const chip = provenanceChip(healthspan);
  return (
    <section aria-label="Healthy life today" className="rounded-panel bg-surface p-panel">
      <p className={`type-hero m-0 ${TONE[hours.tone]}`}>
        <span aria-hidden="true">{hours.text}</span>
        <span className="sr-only">{hours.spoken}</span>
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="type-secondary text-muted">{hoursWord(healthspan.hours_today)}</span>
        {chip ? (
          <span className="type-chip inline-flex min-h-6 items-center rounded-full bg-surface-2 px-2 text-text">
            {chip}
          </span>
        ) : null}
      </div>
      <p className="type-secondary m-0 mt-1 text-text tabular-nums">
        Score {Math.round(healthspan.overall)} · <span aria-hidden="true">{years.text}</span>
        <span className="sr-only">{years.spoken}</span>
      </p>
    </section>
  );
}
