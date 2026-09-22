import { Shell } from "@/components/Shell";
import { FIXTURES } from "@/lib/api";
import { readFixtureView, type SearchParams } from "@/lib/screens";

export default async function ProtocolPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const view = FIXTURES ? readFixtureView(await searchParams) : undefined;
  return <Shell screen="protocol" theme={view?.theme} scale={view?.scale} />;
}
