import { Shell } from "@/components/Shell";
import { Sources } from "@/components/sources/Sources";
import { FIXTURES } from "@/lib/api";
import { readFixtureView, type SearchParams } from "@/lib/screens";

export default async function SourcesPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const view = FIXTURES ? readFixtureView(await searchParams) : undefined;
  return (
    <Shell screen="sources" theme={view?.theme} scale={view?.scale}>
      <Sources />
    </Shell>
  );
}
