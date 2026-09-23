"use client";

import { Shell } from "@/components/Shell";
import { Button, Chip, EmptyState, InsetList, ListRow, MEANING_ICONS, PhotoPanel } from "@/components/ui";
import { deviceById } from "@/content/devices";
import { useDevices } from "@/lib/deviceStore";

export interface DeviceDetailProps {
  id: string;
  /** Fixtures mode only: the screenshot query, carried onto links. */
  query?: string;
  theme?: "light" | "dark";
  scale?: number;
}

/** The api note as a chip: the part before any colon, so it stays one short pill. */
function apiChip(api: string): string {
  return api.split(":")[0].trim();
}

/**
 * One device, pushed from Devices: the hero panel, the name, the connect state
 * and api note as chips, three inset sections, and the one action. Connect and
 * Disconnect flip the placeholder state on this phone only.
 */
export function DeviceDetail({ id, query = "", theme, scale }: DeviceDetailProps) {
  const { connected, setConnected } = useDevices();
  const device = deviceById(id);

  if (!device) {
    return (
      <Shell screen="devices" pushed title="Device" theme={theme} scale={scale}>
        <EmptyState
          text="No device by that name."
          action={<Button href={`/devices${query}`}>Back to devices</Button>}
        />
      </Shell>
    );
  }

  const on = connected.has(device.id);
  const state = on ? "Connected" : "Not connected";

  return (
    <Shell screen="devices" pushed title={device.name} theme={theme} scale={scale}>
      <div className="mt-4">
        <PhotoPanel tint={device.tint} icon={MEANING_ICONS[device.icon]} height={200} />
      </div>

      <h2 className="type-screen-title m-0 mt-4 text-ink">{device.name}</h2>

      <div className="mt-3 flex flex-wrap gap-2">
        <Chip tone={on ? "good" : "neutral"}>{state}</Chip>
        <Chip>{apiChip(device.api)}</Chip>
      </div>

      <div className="mt-section flex flex-col gap-section">
        <InsetList label="What it feeds">
          <ListRow title={device.feeds} />
        </InsetList>

        <InsetList label="Connection">
          <ListRow title={device.api} />
        </InsetList>

        <div>
          <InsetList label="Status">
            <ListRow title={state} detail="Seeded on this phone" />
          </InsetList>
          <p className="type-caption m-0 mt-2 px-4 text-muted">Pairing arrives with the device app</p>
        </div>
      </div>

      {/* The one action, pinned above the home indicator while the sections scroll under it. */}
      <div
        className="sticky bottom-0 -mx-gutter mt-section bg-page px-gutter pt-3"
        style={{ paddingBottom: "calc(8px + env(safe-area-inset-bottom))" }}
      >
        {on ? (
          <Button variant="secondary" onClick={() => setConnected(device.id, false)} className="min-h-11! w-full">
            Disconnect
          </Button>
        ) : (
          <Button onClick={() => setConnected(device.id, true)}>Connect</Button>
        )}
      </div>
    </Shell>
  );
}
