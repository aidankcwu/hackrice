import { Shell } from "@/components/Shell";
import { FIXTURES } from "@/lib/api";
import { readFixtureView, type SearchParams } from "@/lib/screens";

/**
 * Today. In fixtures mode this route also renders any other screen named by
 * `?screen=`, so every screenshot is taken against `/`.
 */
export default async function TodayPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const view = FIXTURES ? readFixtureView(await searchParams) : undefined;
  return <Shell screen={view?.screen ?? "today"} theme={view?.theme} scale={view?.scale} />;
}
