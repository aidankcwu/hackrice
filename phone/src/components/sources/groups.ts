import { RULES, type Rule } from "@/lib/rules";
import { SOURCES, type Grade, type Source } from "@/lib/sources";

/** A group on the Sources screen: a rule's studies, or the studies behind the tests. */
export interface SourceGroup {
  key: string;
  label: string;
  sources: Source[];
  /** The rule the studies belong to; absent for the final group. */
  rule?: Rule;
  /** What the app does with the rule, as finished copy; absent for the final group. */
  consequence?: string;
}

/**
 * A rule's consequence as finished copy. Sleep debt's carries a day count that
 * evaluateDay fills in per day (rules.ts); this screen has no day, so it says
 * the same thing without the number.
 */
export function staticConsequence(rule: Rule): string {
  return rule.consequence.replace(/\bday N of short sleep\b/, "which day of short sleep it is");
}

/** The final group: studies no rule uses (the tests, the pace of aging). */
export const REST_LABEL = "Tests and pace of aging";

/**
 * One group per rule that has sources, in RULES order; a study several rules
 * share appears under each. Studies no rule uses go last, in SOURCES order.
 */
export function sourceGroups(): SourceGroup[] {
  const attached = new Set<string>();
  const groups: SourceGroup[] = [];
  for (const rule of Object.values(RULES)) {
    const sources = rule.sources.flatMap((key) => (key in SOURCES ? [SOURCES[key]] : []));
    if (!sources.length) continue;
    for (const source of sources) attached.add(source.key);
    groups.push({ key: rule.id, label: rule.name, sources, rule, consequence: staticConsequence(rule) });
  }
  const rest = Object.values(SOURCES).filter((source) => !attached.has(source.key));
  if (rest.length) groups.push({ key: "rest", label: REST_LABEL, sources: rest });
  return groups;
}

/** How many studies carry each grade, A to C, counting every study once. */
export function gradeCounts(): { grade: Grade; count: number }[] {
  const counts: Record<Grade, number> = { A: 0, B: 0, C: 0 };
  for (const source of Object.values(SOURCES)) counts[source.grade] += 1;
  return (["A", "B", "C"] as const).map((grade) => ({ grade, count: counts[grade] }));
}

/** The study's page: its DOI, else its PubMed entry, else none. */
export function sourceHref(source: Source): string | undefined {
  if (source.doi) return `https://doi.org/${source.doi}`;
  if (source.pmid) return `https://pubmed.ncbi.nlm.nih.gov/${source.pmid}/`;
  return undefined;
}
