import { Shell } from "@/components/Shell";
import { Concierge } from "@/components/concierge/Concierge";
import { FIXTURES } from "@/lib/api";
import { readFixtureView, type SearchParams } from "@/lib/screens";

export default async function ConciergePage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(params) : undefined;
  return (
    <Shell screen="concierge" theme={view?.theme} scale={view?.scale}>
      <Concierge />
    </Shell>
  );
}
