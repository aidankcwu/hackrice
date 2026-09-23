import { Shell } from "@/components/Shell";
import { TreatmentList } from "@/components/treatments/TreatmentList";
import { FIXTURES } from "@/lib/api";
import { fixtureQuery, readFixtureView, type SearchParams } from "@/lib/screens";

/** Treatments, pushed from the menu. */
export default async function TreatmentsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(params) : undefined;
  return (
    <Shell screen="treatments" theme={view?.theme} scale={view?.scale}>
      <TreatmentList query={FIXTURES ? fixtureQuery(params) : ""} />
    </Shell>
  );
}
