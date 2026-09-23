import { Chip, EmptyState, InsetList } from "@/components/ui";
import { SourceRow } from "./SourceRow";
import { gradeCounts, sourceGroups } from "./groups";

/**
 * The Sources screen: an intro line, the grade counts, then one inset list per
 * rule with what the app does with its studies, and the studies behind the
 * tests last.
 */
export function Sources() {
  const groups = sourceGroups();
  if (!groups.length) return <EmptyState text="No studies on file yet." />;

  return (
    <div className="pt-4">
      <p className="type-secondary m-0 text-muted">Every number in the app, with the study behind it. Grades A to C.</p>
      <ul aria-label="Studies by grade" className="m-0 mt-3 flex list-none flex-wrap gap-2 p-0">
        {gradeCounts().map(({ grade, count }) => (
          <li key={grade}>
            <Chip>{`${grade} · ${count}`}</Chip>
          </li>
        ))}
      </ul>
      <div className="mt-section flex flex-col gap-section">
        {groups.map((group) => (
          <div key={group.key}>
            <InsetList label={group.label}>
              {group.sources.map((source) => (
                <SourceRow key={source.key} source={source} />
              ))}
            </InsetList>
            {group.consequence ? <p className="type-caption m-0 mt-2 px-4 text-muted">What the app does with it: {group.consequence}</p> : null}
            {group.rule?.claim === "assumed" && group.rule.assumed ? (
              <p className="type-caption m-0 mt-1 px-4 text-muted">Assumed: {group.rule.assumed}</p>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}
