import { Button, EmptyState } from "@/components/ui";
import { SCREENS } from "@/lib/screens";
import { CLAIMS } from "@/lib/sources";
import { ClaimCard } from "./ClaimCard";

const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];

/** "Eight things the app will not say." The word follows the count. */
export function claimsIntro(count: number): string {
  const word = count < WORDS.length ? WORDS[count] : String(count);
  return `${word} ${count === 1 ? "thing" : "things"} the app will not say.`;
}

export interface ClaimsProps {
  /** Fixtures mode: the screenshot query, kept on the link to Sources. */
  query?: string;
}

/** The claims screen: an intro line, one card per claim, and a link to every source. */
export function Claims({ query = "" }: ClaimsProps) {
  if (!CLAIMS.length) return <EmptyState text="Nothing on the list yet." />;

  return (
    <div className="pt-4">
      <p className="type-secondary m-0 text-muted">{claimsIntro(CLAIMS.length)}</p>
      <ul className="m-0 mt-4 flex list-none flex-col gap-3 p-0">
        {CLAIMS.map((item) => (
          <ClaimCard key={item.claim} claim={item.claim} truth={item.truth} sources={item.sources} />
        ))}
      </ul>
      <div className="mt-4">
        <Button variant="tertiary" href={`${SCREENS.sources.href}${query}`}>
          All sources
        </Button>
      </div>
    </div>
  );
}
