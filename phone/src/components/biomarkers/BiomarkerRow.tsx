"use client";

import { Button, Chip } from "@/components/ui";
import type { BiomarkerCard } from "@/content/biomarkers";
import { formatDay, formatValue, type BiomarkerReading } from "@/lib/biomarkerStore";

export interface BiomarkerRowProps {
  biomarker: BiomarkerCard;
  reading: BiomarkerReading;
  onAdd: () => void;
}

// The same hairline as a ListRow: rows after the first, inset 56 px.
const HAIRLINE =
  "relative not-first:before:absolute not-first:before:top-0 not-first:before:right-0 not-first:before:left-14 not-first:before:hairline";

/**
 * One marker in a panel's inset list: the name at 17, the last value at 22
 * tabular with its unit at 13, when it was last tested, the target line, the
 * marker's note, and "Add result". A value from the seeded panel carries a chip.
 */
export function BiomarkerRow({ biomarker, reading, onAdd }: BiomarkerRowProps) {
  const tested = reading.lastTested ? `last tested ${formatDay(reading.lastTested)}` : "not yet tested";
  return (
    <li className={HAIRLINE}>
      <div className="flex min-h-row items-start gap-3 px-4 py-3">
        <div className="flex min-w-0 flex-1 flex-col">
          <p className="type-body m-0 text-text">{biomarker.name}</p>
          <p className="type-caption m-0 text-muted tabular-nums">{tested}</p>
          <p className="type-caption m-0 text-muted">Target {biomarker.target}</p>
          {biomarker.note ? <p className="type-caption m-0 text-muted tabular-nums">{biomarker.note}</p> : null}
          <div className="mt-1 self-start">
            <Button variant="tertiary" onClick={onAdd} ariaLabel={`Add result for ${biomarker.name}`}>
              Add result
            </Button>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1 pt-0.5">
          <p className="m-0 flex items-baseline gap-1">
            {reading.value === null ? (
              <>
                <span aria-hidden="true" className="type-section text-muted tabular-nums">
                  —
                </span>
                <span className="sr-only">no value yet</span>
              </>
            ) : (
              <span className="type-section text-ink tabular-nums">{formatValue(reading.value)}</span>
            )}
            <span className="type-caption text-muted">{biomarker.unit}</span>
          </p>
          {reading.seeded ? <Chip>Seeded</Chip> : null}
        </div>
      </div>
    </li>
  );
}
