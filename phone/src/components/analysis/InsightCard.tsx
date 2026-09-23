import { Card, MEANING_ICONS } from "@/components/ui";
import type { Insight } from "@/lib/insights";
import { sourceLine } from "@/lib/sources";
import { dayChip } from "./ClusterCard";

/**
 * One insight: a tinted panel with the icon for its meaning, the finding as
 * the title, the day it is about as a chip, one sentence with the wearer's own
 * numbers and the study's, and the study line.
 */
export function InsightCard({ insight }: { insight: Insight }) {
  return (
    <li>
      <Card tint={insight.tint} icon={MEANING_ICONS[insight.icon]} title={insight.title} chip={dayChip(insight.date)}>
        <p className="type-secondary m-0 mt-3 text-text">{insight.line}</p>
        <p className="type-caption m-0 mt-3 text-muted">{insight.sourceKeys.map(sourceLine).join("; ")}</p>
      </Card>
    </li>
  );
}
