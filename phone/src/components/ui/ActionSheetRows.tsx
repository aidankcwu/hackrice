import type { LucideIcon } from "lucide-react";
import { ICON_SIZES, STROKE } from "./icons";

export interface ActionRowProps {
  label: string;
  onClick: () => void;
  destructive?: boolean;
  icon?: LucideIcon;
}

/** A 56 px row of an action sheet; rows after the first carry a hairline. Destructive reads in --bad. */
export function ActionRow({ label, onClick, destructive = false, icon: Icon }: ActionRowProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`type-body flex min-h-14 w-full items-center gap-3 px-card transition-colors duration-120 not-first:hairline active:bg-surface-2 ${
        Icon ? "justify-start text-left" : "justify-center text-center"
      } ${destructive ? "text-bad" : "text-ink"}`}
    >
      {Icon ? <Icon size={ICON_SIZES.list} strokeWidth={STROKE} aria-hidden="true" /> : null}
      {label}
    </button>
  );
}
