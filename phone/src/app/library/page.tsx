import { Shell } from "@/components/Shell";
import { Library } from "@/components/library/Library";
import { FIXTURES } from "@/lib/api";
import { fixtureQuery, readFixtureView, type SearchParams } from "@/lib/screens";

/** The protocol library, pushed from the menu. */
export default async function LibraryPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(params) : undefined;
  return (
    <Shell screen="library" theme={view?.theme} scale={view?.scale}>
      <Library query={FIXTURES ? fixtureQuery(params) : ""} />
    </Shell>
  );
}
