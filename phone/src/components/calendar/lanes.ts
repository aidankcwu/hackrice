import type { LucideIcon } from "lucide-react";
import { MEANING_ICONS } from "@/components/ui";
import type { LaneId, SegmentState } from "@/lib/calendar";

/** One icon per lane, by meaning, drawn above it instead of a rotated label. */
export const LANE_ICONS: Record<LaneId, LucideIcon> = {
  sleep: MEANING_ICONS.sleep,
  light: MEANING_ICONS.light,
  caffeine: MEANING_ICONS.caffeine,
  food: MEANING_ICONS.food,
  move: MEANING_ICONS.movement,
  screens: MEANING_ICONS.screens,
  peptide: MEANING_ICONS.peptide,
};

/**
 * A protocol window on the day strip: a hairline outline with a 12% fill. Met
 * is good at 20% with a 2 px solid left edge; not met (or not yet) is 12% grey;
 * a missed window is the grey one, with the red X drawn by the strip. The grey
 * is 12% of ink, which is opaque in both schemes; muted is translucent in dark.
 */
export const SEGMENT_FILL: Record<SegmentState, string> = {
  met: "border border-hairline border-l-2 border-l-good bg-good/20",
  open: "border border-hairline bg-ink/12",
  missed: "border border-hairline bg-ink/12",
};

/** The same windows as thin stripes in a week column: fills only, no room for an edge. */
export const STRIPE_FILL: Record<SegmentState, string> = {
  met: "bg-good/20",
  open: "bg-ink/12",
  missed: "bg-ink/12",
};
