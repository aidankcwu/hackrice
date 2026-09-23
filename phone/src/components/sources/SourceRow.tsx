import { ChevronRight } from "lucide-react";
import { Chip, ICON_SIZES, STROKE } from "@/components/ui";
import type { Source } from "@/lib/sources";
import { sourceHref } from "./groups";

/*
 * ListRow's layout, with two things ListRow cannot do: the row opens the study
 * in a new tab (a plain anchor, not next/link), and the finding is a third line.
 * ROW, PRESSED and HAIRLINE are copies of the private constants in
 * src/components/ui/InsetList.tsx (ROW there has no pl-4; ListRow adds it).
 * Change them together, or export them from InsetList and import them here.
 */
const ROW = "flex min-h-row w-full items-center gap-2 pr-4 pl-4 text-left focus-visible:-outline-offset-2";
const PRESSED = "transition-colors duration-120 active:bg-surface-2";
const HAIRLINE =
  "relative not-first:before:absolute not-first:before:top-0 not-first:before:right-0 not-first:before:left-14 not-first:before:hairline";

export interface SourceRowProps {
  source: Source;
}

/** One study: author and year, the journal, the finding, its grade, and a chevron when the study has a page. */
export function SourceRow({ source }: SourceRowProps) {
  const href = sourceHref(source);
  const inner = (
    <>
      <span className="flex min-w-0 flex-1 flex-col py-2">
        <span className="type-body text-text">
          {source.author} {source.year}
        </span>
        {source.journal ? <span className="type-secondary text-muted">{source.journal}</span> : null}
        <span className="type-caption text-muted">{source.finding}</span>
      </span>
      <Chip>
        <span className="sr-only">Grade </span>
        {source.grade}
      </Chip>
      {href ? <ChevronRight size={ICON_SIZES.list} strokeWidth={STROKE} className="shrink-0 text-muted" aria-hidden="true" /> : null}
    </>
  );

  return (
    <li className={HAIRLINE}>
      {href ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className={`${ROW} ${PRESSED}`}>
          {inner}
        </a>
      ) : (
        <div className={ROW}>{inner}</div>
      )}
    </li>
  );
}
