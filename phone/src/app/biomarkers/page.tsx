import { Shell } from "@/components/Shell";
import { EmptyState } from "@/components/ui";
import { FIXTURES } from "@/lib/api";
import { readFixtureView, type SearchParams } from "@/lib/screens";

/** Stub: the screen exists so the menu link resolves; its content comes in a later build. */
export default async function BiomarkersPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const view = FIXTURES ? readFixtureView(await searchParams) : undefined;
  return (
    <Shell screen="biomarkers" theme={view?.theme} scale={view?.scale}>
      <EmptyState text="Coming in this build." />
    </Shell>
  );
}
