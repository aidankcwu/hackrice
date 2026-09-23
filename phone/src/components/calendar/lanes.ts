import { Coffee, Footprints, Monitor, Moon, Sun, Syringe, Utensils, type LucideIcon } from "lucide-react";
import type { LaneId, SegmentState } from "@/lib/calendar";

/** One icon per lane, drawn above it instead of a rotated label. */
export const LANE_ICONS: Record<LaneId, LucideIcon> = {
  sleep: Moon,
  light: Sun,
  caffeine: Coffee,
  food: Utensils,
  move: Footprints,
  screens: Monitor,
  peptide: Syringe,
};

/** Green when the rule was met; grey otherwise. */
export const SEGMENT_FILL: Record<SegmentState, string> = {
  met: "bg-earn/50",
  open: "bg-band",
  missed: "bg-band",
};
