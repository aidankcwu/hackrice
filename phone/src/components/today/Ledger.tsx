import Link from "next/link";
import { clockTime, sentence, type LedgerEntry } from "@/lib/today";
import { FAMILY_ICONS } from "./icons";
import { OutcomeLabel } from "./OutcomeLabel";

/**
 * The timed log: time · family symbol · label · outcome, newest first, between
 * full-width hairlines. Held-back rows go quiet so the ones it spoke on stand
 * out; the footer counts them. A row opens its detail.
 */
export function Ledger({ entries, query }: { entries: LedgerEntry[]; query: string }) {
  const heldBack = entries.filter((entry) => entry.outcome === "held back").length;
  return (
    <section aria-labelledby="ledger-title" className="mt-section">
      <h2 id="ledger-title" className="type-title m-0 mb-2 text-ink">
        Today
      </h2>
      <ul className="m-0 list-none border-b-[0.5px] border-line p-0">
        {entries.map((entry) => (
          <li key={entry.id} className="border-t-[0.5px] border-line">
            <LedgerRow entry={entry} href={`/decision/${encodeURIComponent(entry.id)}${query}`} />
          </li>
        ))}
      </ul>
      <p className="type-caption m-0 mt-4 text-muted">Held back {heldBack} today</p>
    </section>
  );
}

function LedgerRow({ entry, href }: { entry: LedgerEntry; href: string }) {
  const held = entry.outcome === "held back";
  const time = clockTime(entry.t);
  const label = sentence(entry.label);
  const Icon = entry.family ? FAMILY_ICONS[entry.family] : null;
  return (
    <Link
      href={href}
      aria-label={`${time}, ${label}, ${entry.outcome}`}
      className="flex min-h-[52px] items-center py-2 transition-colors duration-150 active:bg-surface-2"
    >
      <span className="type-secondary w-[calc(40px*var(--type-scale))] shrink-0 whitespace-nowrap text-muted tabular-nums">
        {time}
      </span>
      <span className="ml-2 flex w-[calc(18px*var(--type-scale))] shrink-0 justify-center text-muted">
        {Icon ? <Icon className="size-[calc(18px*var(--type-scale))]" strokeWidth={2} aria-hidden="true" /> : null}
      </span>
      <span className="ml-4 flex min-w-0 flex-1 flex-wrap items-center justify-between gap-x-2">
        <span className={`type-body min-w-0 ${held ? "text-muted" : "text-text"}`}>{label}</span>
        <OutcomeLabel outcome={entry.outcome} />
      </span>
    </Link>
  );
}
