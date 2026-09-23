import { Shell } from "@/components/Shell";
import { Analysis } from "@/components/analysis/Analysis";
import { Calendar } from "@/components/calendar/Calendar";
import { AddItem } from "@/components/protocol/AddItem";
import { AddItemLink, Protocol } from "@/components/protocol/Protocol";
import { Settings } from "@/components/settings/Settings";
import { Today } from "@/components/today/Today";
import { EmptyState } from "@/components/ui";
import { FIXTURES } from "@/lib/api";
import { fixtureQuery, readFixtureView, type ScreenId, type SearchParams } from "@/lib/screens";
import { APP_VERSION } from "@/lib/version";

/** Menu screens that are still stubs; each gets its content in a later build. */
const STUBS = new Set<ScreenId>(["library", "treatments", "tests", "biomarkers", "devices", "concierge", "sources", "claims", "find"]);

/**
 * Today. In fixtures mode this route also renders any other screen named by
 * `?screen=`, so every screenshot is taken against `/` (`additem` is Add item).
 */
export default async function TodayPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(params) : undefined;
  const screen = view?.screen ?? "today";
  const query = FIXTURES ? fixtureQuery(params) : "";
  if (view?.state.startsWith("additem")) return <AddItem query={query} theme={view.theme} scale={view.scale} />;
  return (
    <Shell
      screen={screen}
      theme={view?.theme}
      scale={view?.scale}
      action={screen === "protocol" ? <AddItemLink query={query} /> : undefined}
    >
      {screen === "today" ? <Today query={query} /> : null}
      {screen === "calendar" ? <Calendar /> : null}
      {screen === "analysis" ? <Analysis /> : null}
      {screen === "protocol" ? <Protocol query={query} /> : null}
      {screen === "settings" ? <Settings version={APP_VERSION} /> : null}
      {STUBS.has(screen) ? <EmptyState text="Coming in this build." /> : null}
    </Shell>
  );
}
