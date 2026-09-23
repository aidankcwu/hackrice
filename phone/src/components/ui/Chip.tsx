import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { STROKE } from "./icons";

export type ChipTone = "neutral" | "good" | "bad" | "warn";

export interface ChipProps {
  tone?: ChipTone;
  icon?: LucideIcon;
  children: ReactNode;
}

/** Data chips carry the sign or the word inside; the fill never carries the meaning alone. */
const TONE: Record<ChipTone, string> = {
  neutral: "bg-surface-2 text-text",
  good: "bg-good-soft text-good",
  bad: "bg-bad-soft text-bad",
  warn: "bg-warn-soft text-warn",
};

/** A 28 px pill: provenance, counts and signed values. */
export function Chip({ tone = "neutral", icon: Icon, children }: ChipProps) {
  return (
    <span className={`type-chip inline-flex min-h-7 items-center gap-1 rounded-full px-3 whitespace-nowrap tabular-nums ${TONE[tone]}`}>
      {Icon ? <Icon size={14} strokeWidth={STROKE} aria-hidden="true" /> : null}
      {children}
    </span>
  );
}
