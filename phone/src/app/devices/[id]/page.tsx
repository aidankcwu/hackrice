import { DeviceDetail } from "@/components/devices/DeviceDetail";
import { FIXTURES } from "@/lib/api";
import { fixtureQuery, readFixtureView, type SearchParams } from "@/lib/screens";

/** One device, pushed from Devices: `/devices/<device id>`. */
export default async function DevicePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { id } = await params;
  const query = FIXTURES ? await searchParams : {};
  const view = FIXTURES ? readFixtureView(query) : undefined;
  return <DeviceDetail id={id} query={FIXTURES ? fixtureQuery(query) : ""} theme={view?.theme} scale={view?.scale} />;
}
