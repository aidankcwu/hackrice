"use client";

import { ICON_SIZES, MEANING_ICONS, STROKE } from "@/components/ui";
import type { IconName } from "@/content/types";

export interface OptionPillProps {
  icon: IconName;
  label: string;
  selected: boolean;
  onSelect: () => void;
}

/**
 * One answer: a 64-tall pill on surface-2 with the option's icon at 24 and its
 * label at 17. Selected: a 2 px ink outline and a check at the right, 200 ms.
 * The border is always drawn (transparent when off) so the row never moves.
 */
export function OptionPill({ icon, label, selected, onSelect }: OptionPillProps) {
  const Icon = MEANING_ICONS[icon];
  const Check = MEANING_ICONS.check;
  return (
    <li>
      <button
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
        className={`motion pressable flex min-h-16 w-full items-center gap-3 rounded-full border-2 bg-surface-2 py-2 pr-5 pl-5 text-left ${
          selected ? "border-ink" : "border-transparent"
        }`}
      >
        <Icon
          size={ICON_SIZES.card}
          strokeWidth={STROKE}
          className={`motion shrink-0 ${selected ? "text-ink" : "text-muted"}`}
          aria-hidden="true"
        />
        <span className="type-card-title min-w-0 flex-1 text-text">{label}</span>
        {selected ? <Check size={ICON_SIZES.card} strokeWidth={STROKE} className="shrink-0 text-ink" aria-hidden="true" /> : null}
      </button>
    </li>
  );
}
