"use client";

import { Toggle } from "@/components/library/Toggle";

export interface SwitchRowProps {
  id: string;
  label: string;
  /** A 15 muted line under the label. */
  line?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

// The same hairline as a ListRow: rows after the first, inset 56 px.
const HAIRLINE =
  "relative not-first:before:absolute not-first:before:top-0 not-first:before:right-0 not-first:before:left-14 not-first:before:hairline";

/** A 56 row of an InsetList: label (and line) at the left, the system's Toggle at the right. The label is the switch's name; tapping it toggles too. */
export function SwitchRow({ id, label, line, checked, onChange }: SwitchRowProps) {
  return (
    <li className={HAIRLINE}>
      <div className="flex min-h-row items-center gap-4 px-4 py-1.5">
        <label htmlFor={id} className="flex min-w-0 flex-1 cursor-pointer flex-col py-1">
          <span className="type-body text-text">{label}</span>
          {line ? <span className="type-secondary text-muted">{line}</span> : null}
        </label>
        <Toggle id={id} on={checked} onChange={onChange} label={label} />
      </div>
    </li>
  );
}
