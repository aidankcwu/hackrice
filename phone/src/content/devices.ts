import type { Device } from "./types";

// Connect state is a placeholder: only the glasses read as connected until
// a real pairing flow exists. Order matches the product spec.
export const DEVICES: Device[] = [
  {
    id: "rayban-meta",
    name: "Ray-Ban Meta glasses",
    feeds: "What you did",
    api: "Camera and voice on the frame",
    connected: true,
    tint: "stone",
    icon: "glasses",
  },
  {
    id: "whoop-oura",
    name: "WHOOP 5.0 or Oura Ring 5",
    feeds: "Sleep, HRV, RHR",
    api: "Both have APIs",
    connected: false,
    tint: "sage",
    icon: "watch",
  },
  {
    id: "apple-watch",
    name: "Apple Watch",
    feeds: "HealthKit",
    api: "On-device only",
    connected: false,
    tint: "slate",
    icon: "watch",
  },
  {
    id: "stelo-lingo",
    name: "Dexcom Stelo or Abbott Lingo",
    feeds: "Over-the-counter glucose, 14–15 day sensor",
    api: "No public API yet: manual or screenshot import",
    connected: false,
    tint: "sand",
    icon: "biomarker",
  },
  {
    id: "aranet4",
    name: "Aranet4",
    feeds: "Room CO2",
    api: "Open Bluetooth protocol",
    connected: false,
    tint: "bluegrey",
    icon: "air",
  },
  {
    id: "withings",
    name: "Withings scale and BP",
    feeds: "Weight, body composition, blood pressure",
    api: "API",
    connected: false,
    tint: "stone",
    icon: "body",
  },
  {
    id: "airthings-awair",
    name: "Airthings or Awair",
    feeds: "Indoor air",
    api: "API",
    connected: false,
    tint: "bluegrey",
    icon: "air",
  },
];

export function deviceById(id: string): Device | undefined {
  return DEVICES.find((d) => d.id === id);
}
