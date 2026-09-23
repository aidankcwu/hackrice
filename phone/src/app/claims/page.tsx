import { Shell } from "@/components/Shell";
import { Claims } from "@/components/claims/Claims";
import { FIXTURES } from "@/lib/api";
import { fixtureQuery, readFixtureView, type SearchParams } from "@/lib/screens";

export default async function ClaimsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(params) : undefined;
  return (
    <Shell screen="claims" theme={view?.theme} scale={view?.scale}>
      <Claims query={FIXTURES ? fixtureQuery(params) : ""} />
    </Shell>
  );
}
