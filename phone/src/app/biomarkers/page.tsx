import { Shell } from "@/components/Shell";
import { Biomarkers } from "@/components/biomarkers/Biomarkers";
import { FIXTURES } from "@/lib/api";
import { readFixtureView, type SearchParams } from "@/lib/screens";

export default async function BiomarkersPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const view = FIXTURES ? readFixtureView(await searchParams) : undefined;
  return (
    <Shell screen="biomarkers" theme={view?.theme} scale={view?.scale}>
      <Biomarkers />
    </Shell>
  );
}
