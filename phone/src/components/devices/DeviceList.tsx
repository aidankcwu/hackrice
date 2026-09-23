"use client";

import { Button, Card, MEANING_ICONS } from "@/components/ui";
import { DEVICES } from "@/content/devices";
import { useDevices } from "@/lib/deviceStore";

export interface DeviceListProps {
  /** Fixtures mode only: the screenshot query, carried onto each detail link. */
  query?: string;
}

/**
 * Devices, pushed from the menu: one card per device the app can read, its
 * connect state and what it feeds. The whole card opens the detail.
 */
export function DeviceList({ query = "" }: DeviceListProps) {
  const { connected } = useDevices();

  return (
    <div className="mt-2">
      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        {DEVICES.map((device) => {
          const on = connected.has(device.id);
          return (
            <li key={device.id}>
              <Card
                href={`/devices/${device.id}${query}`}
                tint={device.tint}
                icon={MEANING_ICONS[device.icon]}
                photoHeight={120}
                title={device.name}
                line={device.feeds}
                chip={on ? "Connected" : "Not connected"}
                chipTone={on ? "good" : "neutral"}
                action={<Button variant="tertiary">Details</Button>}
              />
            </li>
          );
        })}
      </ul>
      <p className="type-caption m-0 mt-4 text-muted">Connect state is seeded until pairing arrives with the device app</p>
    </div>
  );
}
