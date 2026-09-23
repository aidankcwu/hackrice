import { Shell } from "@/components/Shell";
import { Tests } from "@/components/tests/Tests";
import { FIXTURES } from "@/lib/api";
import { readFixtureView, type SearchParams } from "@/lib/screens";

export default async function TestsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const view = FIXTURES ? readFixtureView(await searchParams) : undefined;
  return (
    <Shell screen="tests" theme={view?.theme} scale={view?.scale}>
      <Tests />
    </Shell>
  );
}
