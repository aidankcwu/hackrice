import { Shell } from "@/components/Shell";
import { Today } from "@/components/today/Today";
import { FIXTURES } from "@/lib/api";
import { fixtureQuery, readFixtureView, type SearchParams } from "@/lib/screens";

/**
 * Today. In fixtures mode this route also renders any other screen named by
 * `?screen=`, so every screenshot is taken against `/`.
 */
export default async function TodayPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(params) : undefined;
  const screen = view?.screen ?? "today";
  return (
    <Shell screen={screen} theme={view?.theme} scale={view?.scale}>
      {screen === "today" ? <Today query={FIXTURES ? fixtureQuery(params) : ""} /> : null}
    </Shell>
  );
}
