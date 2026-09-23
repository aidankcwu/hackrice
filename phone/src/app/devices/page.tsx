import { Shell } from "@/components/Shell";
import { DeviceList } from "@/components/devices/DeviceList";
import { FIXTURES } from "@/lib/api";
import { fixtureQuery, readFixtureView, type SearchParams } from "@/lib/screens";

/** Devices, pushed from the menu: every device the app can read, with its connect state and what it feeds. */
export default async function DevicesPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(params) : undefined;
  return (
    <Shell screen="devices" theme={view?.theme} scale={view?.scale}>
      <DeviceList query={FIXTURES ? fixtureQuery(params) : ""} />
    </Shell>
  );
}
