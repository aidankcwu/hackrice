import type { ReactNode } from "react";
import { Chip, ICON_SIZES, MEANING_ICONS, STROKE } from "@/components/ui";
import type { ProtocolItemTemplate } from "@/content/types";
import { CLINICIAN_DOSE_LINE, itemMeta, kindMeaning } from "./format";

export interface ItemRowProps {
  item: ProtocolItemTemplate;
  /** Whether the muted line ends with what the glasses see. */
  verify?: boolean;
  /** A control at the right (the review's toggle). */
  trailing?: ReactNode;
}

// The same row and hairline as a ListRow, with room for the clinician chip under the line.
const HAIRLINE =
  "relative not-first:before:absolute not-first:before:top-0 not-first:before:right-0 not-first:before:left-14 not-first:before:hairline";

/**
 * One protocol item in an inset list: the kind's icon, the name at 17, one 15
 * muted line (window · days · cycle · what the glasses see), and "dose set by
 * your clinician" as a chip where a dose exists. Never a dose amount.
 */
export function ItemRow({ item, verify = true, trailing }: ItemRowProps) {
  const Icon = MEANING_ICONS[kindMeaning(item)];
  return (
    <li className={HAIRLINE}>
      <div className="flex min-h-row w-full items-center gap-2 py-2 pr-4 pl-3">
        <span aria-hidden="true" className="grid size-9 shrink-0 place-items-center rounded-full bg-muted/20 text-muted">
          <Icon size={ICON_SIZES.list} strokeWidth={STROKE} />
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="type-body text-text">{item.name}</span>
          <span className="type-secondary text-muted tabular-nums">{itemMeta(item, { verify })}</span>
          {item.clinicianDose ? (
            <span className="mt-1.5">
              <Chip>{CLINICIAN_DOSE_LINE}</Chip>
            </span>
          ) : null}
        </span>
        {trailing}
      </div>
    </li>
  );
}
