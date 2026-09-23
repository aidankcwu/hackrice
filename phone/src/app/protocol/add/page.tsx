import { AddItem } from "@/components/protocol/AddItem";
import { FIXTURES } from "@/lib/api";
import { fixtureQuery, readFixtureView, type SearchParams } from "@/lib/screens";

/** Add item, pushed from Protocol's "+". */
export default async function AddItemPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(params) : undefined;
  return <AddItem query={FIXTURES ? fixtureQuery(params) : ""} theme={view?.theme} scale={view?.scale} />;
}
