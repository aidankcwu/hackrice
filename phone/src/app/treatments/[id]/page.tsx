import { Shell } from "@/components/Shell";
import { TreatmentDetail } from "@/components/treatments/TreatmentDetail";
import { Button, EmptyState } from "@/components/ui";
import { treatmentById } from "@/content/treatments";
import { FIXTURES } from "@/lib/api";
import { fixtureQuery, readFixtureView, type SearchParams } from "@/lib/screens";

/** One treatment: `/treatments/<treatment id>`, pushed from Treatments. */
export default async function TreatmentPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { id } = await params;
  const search = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(search) : undefined;
  const query = FIXTURES ? fixtureQuery(search) : "";
  const treatment = treatmentById(id);

  if (!treatment) {
    return (
      <Shell screen="treatments" pushed title="Treatments" theme={view?.theme} scale={view?.scale}>
        <EmptyState text="There is no treatment with this name." action={<Button href={`/treatments${query}`}>Back to treatments</Button>} />
      </Shell>
    );
  }

  return (
    <Shell screen="treatments" pushed title={treatment.name} theme={view?.theme} scale={view?.scale}>
      <TreatmentDetail treatment={treatment} query={query} />
    </Shell>
  );
}
