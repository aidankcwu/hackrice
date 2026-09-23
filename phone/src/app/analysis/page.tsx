import { Shell } from "@/components/Shell";
import { Analysis } from "@/components/analysis/Analysis";
import { FIXTURES } from "@/lib/api";
import { readFixtureView, type SearchParams } from "@/lib/screens";

export default async function AnalysisPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(params) : undefined;
  return (
    <Shell screen="analysis" theme={view?.theme} scale={view?.scale}>
      <Analysis />
    </Shell>
  );
}
