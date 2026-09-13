import {
  Activity,
  Brain,
  Clock,
  Coffee,
  Dumbbell,
  Eye,
  Flame,
  Footprints,
  Moon,
  Smartphone,
  Sun,
  Trees,
  Users,
  Utensils,
  Wind,
  Wine,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { IconName } from "@/lib/score/types";

/**
 * `IconName` (data layer) → lucide component. The data layer never imports
 * lucide. One icon per layer, fixed by the SKILL.md table: Clock=Clock,
 * Light=Sun, People=Users, Outside=Trees, Air=Wind, Mind=Brain,
 * Body=Footprints, Sleep=Moon, Fuel=Utensils, Recovery=Flame.
 */
export const ICONS: Record<IconName, LucideIcon> = {
  clock: Clock,
  sun: Sun,
  users: Users,
  footprints: Footprints,
  moon: Moon,
  trees: Trees,
  wind: Wind,
  brain: Brain,
  wine: Wine,
  coffee: Coffee,
  smartphone: Smartphone,
  flame: Flame,
  activity: Activity,
  utensils: Utensils,
  dumbbell: Dumbbell,
  eye: Eye,
};

export interface IconProps {
  name: IconName;
  /** 18px in rows, 20px in tiles (SKILL.md); pins use 28. */
  size?: number;
  strokeWidth?: number;
  color?: string;
  className?: string;
}

/**
 * Always decorative: every icon in this UI sits beside visible text, so it is
 * hidden from assistive tech. An unknown name (stale payload) falls back to
 * the eye rather than crashing the panel.
 */
export function Icon({ name, size = 18, strokeWidth = 2, color, className }: IconProps) {
  const Cmp = (ICONS as Partial<Record<string, LucideIcon>>)[name] ?? Eye;
  return <Cmp size={size} strokeWidth={strokeWidth} color={color} className={className} aria-hidden="true" focusable="false" />;
}
