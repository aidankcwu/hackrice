import { DecisionDetail } from "@/components/today/DecisionDetail";
import { FIXTURES } from "@/lib/api";
import { readFixtureView, type SearchParams } from "@/lib/screens";

/** A ledger row opened from Today: `/decision/<episode or decision id>`. */
export default async function DecisionPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { id } = await params;
  const view = FIXTURES ? readFixtureView(await searchParams) : undefined;
  return <DecisionDetail id={id} theme={view?.theme} scale={view?.scale} />;
}
