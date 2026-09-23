import type { LucideIcon } from "lucide-react";
import { ICON_SIZES, STROKE } from "./icons";

export type Tint = "stone" | "sage" | "sand" | "slate" | "bluegrey";
export type PhotoHeight = 96 | 120 | 200;

export interface PhotoPanelProps {
  tint?: Tint;
  icon: LucideIcon;
  height?: PhotoHeight;
  /** A caption in the corner; also the panel's accessible name. */
  label?: string;
}

const TINT: Record<Tint, string> = {
  stone: "bg-tint-stone",
  sage: "bg-tint-sage",
  sand: "bg-tint-sand",
  slate: "bg-tint-slate",
  bluegrey: "bg-tint-bluegrey",
};

const HEIGHT: Record<PhotoHeight, string> = { 96: "h-24", 120: "h-30", 200: "h-50" };

/** A flat tinted panel with one icon: the picture on a card, finished on its own. */
export function PhotoPanel({ tint = "stone", icon: Icon, height = 96, label }: PhotoPanelProps) {
  return (
    <div
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={`relative flex w-full items-center justify-center overflow-hidden rounded-photo ${TINT[tint]} ${HEIGHT[height]}`}
    >
      <Icon size={ICON_SIZES.panel} strokeWidth={STROKE} className="text-ink opacity-70" aria-hidden="true" />
      {label ? <span className="type-chip absolute bottom-3 left-4 text-ink/70">{label}</span> : null}
    </div>
  );
}
