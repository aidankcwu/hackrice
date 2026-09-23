import { Shell } from "@/components/Shell";
import { Settings } from "@/components/settings/Settings";
import { FIXTURES } from "@/lib/api";
import { readFixtureView, type SearchParams } from "@/lib/screens";
import { APP_VERSION } from "@/lib/version";

export default async function SettingsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const view = FIXTURES ? readFixtureView(await searchParams) : undefined;
  return (
    <Shell screen="settings" title="Account" theme={view?.theme} scale={view?.scale}>
      <Settings version={APP_VERSION} />
    </Shell>
  );
}
