import { FindFlow } from "@/components/find/FindFlow";
import { FIND_STEPS } from "@/content/find";
import { FIXTURES } from "@/lib/api";
import { readFixtureView, type SearchParams } from "@/lib/screens";

/**
 * Find my protocol, full screen (no Shell): six steps, then the generated
 * protocol's review. Fixtures mode can open on a step by name, `?screen=find-3`,
 * or on the result with `?screen=find-review`, both with seeded answers.
 */
export default async function FindPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const view = FIXTURES ? readFixtureView(await searchParams) : undefined;
  return <FindFlow start={startFor(view?.state)} theme={view?.theme} scale={view?.scale} />;
}

/** The step a fixtures state name opens on; undefined for the first step. */
function startFor(state: string | undefined): number | undefined {
  if (!state) return undefined;
  if (state === "find-review") return FIND_STEPS.length + 1;
  const match = /^find-([1-9])$/.exec(state);
  const step = match ? Number(match[1]) : NaN;
  return step >= 1 && step <= FIND_STEPS.length ? step : undefined;
}
