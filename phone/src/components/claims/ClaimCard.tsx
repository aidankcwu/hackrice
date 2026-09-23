import { sourceLine } from "@/lib/sources";

export interface ClaimCardProps {
  claim: string;
  truth: string;
  /** Keys into SOURCES. */
  sources: string[];
}

/** One thing the app does not say: the claim struck through, what the studies say instead, and the studies. */
export function ClaimCard({ claim, truth, sources }: ClaimCardProps) {
  return (
    <li className="rounded-card bg-surface p-card">
      <p className="type-body m-0 text-muted">
        <span className="sr-only">Not said: </span>
        <s className="line-through">{claim}</s>
      </p>
      <p className="type-body m-0 mt-2 text-text">{truth}</p>
      {sources.length ? <p className="type-caption m-0 mt-3 text-muted">{sources.map(sourceLine).join(" · ")}</p> : null}
    </li>
  );
}
