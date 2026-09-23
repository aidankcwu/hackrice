"use client";

import { CATEGORY_CHIPS } from "@/content/treatments";
import type { TreatmentCategory } from "@/content/types";

export type FilterCategory = TreatmentCategory | "all";

export interface FilterChipsProps {
  value: FilterCategory;
  onChange: (next: FilterCategory) => void;
}

// A 28 px pill whose tap target reaches 44 through an invisible 8 px band above and below.
const CHIP =
  "type-chip pressable motion relative inline-flex h-7 shrink-0 snap-start items-center rounded-full px-3 whitespace-nowrap before:absolute before:inset-x-0 before:-inset-y-2 before:content-['']";
const ON = "bg-ink text-page";
const OFF = "bg-surface-2 text-text";

/**
 * The filter row above the treatment cards: All · Longevity · Weight ·
 * Recovery · Sleep · Hormones · Supplements, scrolling sideways past the page
 * gutter with 8 px between chips and 16 px before the first and after the
 * last. The chosen chip is ink on the page; the rest sit on surface-2. No
 * scrollbar.
 */
export function FilterChips({ value, onChange }: FilterChipsProps) {
  return (
    <div
      role="group"
      aria-label="Filter by category"
      className="-mx-gutter mt-2 flex snap-x gap-2 overflow-x-auto px-gutter py-2 scroll-px-gutter [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      {CATEGORY_CHIPS.map((chip) => {
        const on = chip.id === value;
        return (
          <button key={chip.id} type="button" aria-pressed={on} onClick={() => onChange(chip.id)} className={`${CHIP} ${on ? ON : OFF}`}>
            {chip.label}
          </button>
        );
      })}
    </div>
  );
}
