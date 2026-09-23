"use client";

import { useState } from "react";
import { Button, Card, Chip, EmptyState, MEANING_ICONS } from "@/components/ui";
import { CATEGORY_CARD_CHIP, STATUS_LABEL, treatmentsFor } from "@/content/treatments";
import { FilterChips, type FilterCategory } from "./FilterChips";

export interface TreatmentListProps {
  /** Fixtures mode only: the screenshot params to carry onto a pushed detail. */
  query?: string;
}

/**
 * Treatments, pushed from the menu: the category filter row, then one card per
 * treatment in the catalogue's order, the menu's anatomy at 120 px, the line
 * "timing · route", the category and status chips, and "Details" onto the
 * treatment. Never a dose amount.
 */
export function TreatmentList({ query = "" }: TreatmentListProps) {
  const [category, setCategory] = useState<FilterCategory>("all");
  const treatments = treatmentsFor(category);

  return (
    <>
      <FilterChips value={category} onChange={setCategory} />
      <p role="status" className="sr-only">
        {treatments.length} {treatments.length === 1 ? "treatment" : "treatments"}
      </p>

      {treatments.length === 0 ? (
        <EmptyState
          text="No treatments in this group yet."
          action={
            <Button onClick={() => setCategory("all")}>Show all</Button>
          }
        />
      ) : (
        <ul className="m-0 mt-2 flex list-none flex-col gap-3 p-0">
          {treatments.map((treatment) => (
            <li key={treatment.id}>
              <Card
                href={`/treatments/${treatment.id}${query}`}
                tint={treatment.tint}
                icon={MEANING_ICONS[treatment.icon]}
                photoHeight={120}
                title={treatment.name}
                line={`${treatment.timing} · ${treatment.route}`}
                action={<Button variant="tertiary">Details</Button>}
              >
                <div className="mt-3 flex flex-wrap gap-2">
                  <Chip>{CATEGORY_CARD_CHIP[treatment.categories[0]]}</Chip>
                  <Chip>{STATUS_LABEL[treatment.status]}</Chip>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      <p className="type-caption m-0 mt-4 text-muted">Where a dose exists it is set by your clinician; the app never shows an amount</p>
    </>
  );
}
