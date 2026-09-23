import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { EmptyState, ICON_SIZES, InsetList, MEANING_ICONS, STROKE } from "@/components/ui";
import { ledgerDetail, sentence, type LedgerEntry } from "@/lib/today";
import { FAMILY_ICONS } from "./icons";
import { OutcomeLabel } from "./OutcomeLabel";

/**
 * The timed log as an inset list, newest first: the family icon in the circle,
 * the label at 17, the time at 13 tabular muted under it, the outcome as a
 * chip. A row opens its detail; the footer counts the held-back ones. With no
 * rows yet, the `empty` instruction stands in their place. The row is drawn
 * here rather than by ListRow, whose detail line is 15.
 */
export function Ledger({ entries, query, empty }: { entries: LedgerEntry[]; query: string; empty: string }) {
  const heldBack = entries.filter((entry) => entry.outcome === "held back").length;
  return (
    <section aria-labelledby="ledger-title" className="mt-section">
      <h2 id="ledger-title" className="type-section m-0 mb-3 text-ink">
        Log
      </h2>
      {entries.length === 0 ? (
        <EmptyState text={empty} />
      ) : (
        <>
          <InsetList>
            {entries.map((entry) => {
              const Icon = entry.family ? FAMILY_ICONS[entry.family] : MEANING_ICONS.glasses;
              return (
                <li
                  key={entry.id}
                  className="relative not-first:before:absolute not-first:before:top-0 not-first:before:right-0 not-first:before:left-14 not-first:before:hairline"
                >
                  <Link
                    href={`/decision/${encodeURIComponent(entry.id)}${query}`}
                    className="flex min-h-row w-full items-center gap-2 pr-4 pl-3 text-left transition-colors duration-120 focus-visible:-outline-offset-2 active:bg-surface-2"
                  >
                    <span aria-hidden="true" className="grid size-9 shrink-0 place-items-center rounded-full bg-muted/20 text-muted">
                      <Icon size={ICON_SIZES.list} strokeWidth={STROKE} />
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col py-2">
                      <span className="type-body text-text">{sentence(entry.label)}</span>
                      <span className="type-caption text-muted tabular-nums">{ledgerDetail(entry)}</span>
                    </span>
                    <OutcomeLabel outcome={entry.outcome} />
                    <ChevronRight size={ICON_SIZES.list} strokeWidth={STROKE} className="shrink-0 text-muted" aria-hidden="true" />
                  </Link>
                </li>
              );
            })}
          </InsetList>
          <p className="type-caption m-0 mt-3 text-muted tabular-nums">Held back {heldBack} today</p>
        </>
      )}
    </section>
  );
}
