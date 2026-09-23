import { NumberBadge } from "@/components/calendar/Columns";
import { Chip } from "@/components/ui";
import type { Cluster } from "@/lib/analysis";
import { RULES } from "@/lib/rules";
import { sourceLine } from "@/lib/sources";

/** "Wed 16". */
export function dayChip(date: string): string {
  const d = new Date(`${date}T12:00:00`);
  return `${d.toLocaleDateString("en-US", { weekday: "short" })} ${d.getDate()}`;
}

/**
 * One numbered cluster: the badge that marks its bars in the columns, the rule
 * as the title, its days as chips, one sentence on the body and one on the
 * mind, and the studies behind the rule.
 */
export function ClusterCard({ cluster }: { cluster: Cluster }) {
  const sources = RULES[cluster.rule].sources.map(sourceLine);
  return (
    <li className="rounded-card bg-surface p-card">
      <div className="flex items-center gap-3">
        <NumberBadge n={cluster.number} size={18} />
        <h2 className="type-card-title m-0 text-ink">{cluster.title}</h2>
      </div>
      <ul className="m-0 mt-3 flex list-none flex-wrap gap-2 p-0" aria-label={`Days: ${cluster.days}`}>
        {cluster.dates.map((date) => (
          <li key={date}>
            <Chip>{dayChip(date)}</Chip>
          </li>
        ))}
      </ul>
      <p className="type-secondary m-0 mt-3 text-text">
        <span className="font-semibold text-ink">Body. </span>
        {cluster.body}
      </p>
      <p className="type-secondary m-0 mt-2 text-text">
        <span className="font-semibold text-ink">Mind. </span>
        {cluster.mind}
      </p>
      <p className="type-caption m-0 mt-3 text-muted">{sources.length ? sources.join("; ") : "Protocol default, no study"}</p>
    </li>
  );
}
