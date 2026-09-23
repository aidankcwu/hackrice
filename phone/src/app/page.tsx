import { Shell } from "@/components/Shell";
import { Analysis } from "@/components/analysis/Analysis";
import { Biomarkers } from "@/components/biomarkers/Biomarkers";
import { Calendar } from "@/components/calendar/Calendar";
import { Claims } from "@/components/claims/Claims";
import { Concierge } from "@/components/concierge/Concierge";
import { DeviceList } from "@/components/devices/DeviceList";
import { FindFlow } from "@/components/find/FindFlow";
import { Library } from "@/components/library/Library";
import { AddItem } from "@/components/protocol/AddItem";
import { AddItemLink, Protocol } from "@/components/protocol/Protocol";
import { Settings } from "@/components/settings/Settings";
import { Sources } from "@/components/sources/Sources";
import { Tests } from "@/components/tests/Tests";
import { Today } from "@/components/today/Today";
import { TreatmentList } from "@/components/treatments/TreatmentList";
import { FIXTURES } from "@/lib/api";
import { fixtureQuery, readFixtureView, type SearchParams } from "@/lib/screens";
import { APP_VERSION } from "@/lib/version";

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
  if (screen === "find") return <FindFlow theme={view?.theme} scale={view?.scale} />;
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
      {screen === "library" ? <Library query={query} /> : null}
      {screen === "treatments" ? <TreatmentList query={query} /> : null}
      {screen === "tests" ? <Tests /> : null}
      {screen === "biomarkers" ? <Biomarkers /> : null}
      {screen === "devices" ? <DeviceList query={query} /> : null}
      {screen === "concierge" ? <Concierge /> : null}
      {screen === "sources" ? <Sources /> : null}
      {screen === "claims" ? <Claims query={query} /> : null}
    </Shell>
  );
}
