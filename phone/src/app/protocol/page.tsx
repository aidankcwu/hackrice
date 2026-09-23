import { Shell } from "@/components/Shell";
import { AddItemLink, Protocol } from "@/components/protocol/Protocol";
import { FIXTURES } from "@/lib/api";
import { fixtureQuery, readFixtureView, type SearchParams } from "@/lib/screens";

export default async function ProtocolPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(params) : undefined;
  const query = FIXTURES ? fixtureQuery(params) : "";
  return (
    <Shell screen="protocol" theme={view?.theme} scale={view?.scale} action={<AddItemLink query={query} />}>
      <Protocol query={query} />
    </Shell>
  );
}
