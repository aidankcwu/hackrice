import { notFound } from "next/navigation";
import { TestRun } from "@/components/tests/TestRun";
import { TESTS } from "@/content/tests";
import { FIXTURES } from "@/lib/api";
import { readFixtureView, type SearchParams } from "@/lib/screens";

/** One test, full screen (no Shell): `/tests/<pvt|nback|dsst|stroop>`. Guided start, the run, then the result. */
export default async function TestPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { id } = await params;
  const test = TESTS.find((candidate) => candidate.id === id);
  if (!test) notFound();
  const view = FIXTURES ? readFixtureView(await searchParams) : undefined;
  return <TestRun id={test.id} theme={view?.theme} scale={view?.scale} />;
}
