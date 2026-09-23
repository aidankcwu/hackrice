/**
 * How a protocol item reads on a screen: its window, days, cycle, what the
 * glasses verify, and the icon and section for its kind. Plain functions, no
 * React, shared by the library's detail and review and by Find my protocol.
 */
import type { Meaning } from "@/components/ui";
import { CLINICIAN_DOSE_LINE, WEEKDAY_LABELS, isAnchored } from "@/content/protocols";
import type { Anchored, ItemKind, ProtocolItemTemplate, ProtocolTemplate, Verify, Weekday, Window } from "@/content/types";

export { CLINICIAN_DOSE_LINE };

const WEEKDAY_NAMES: Record<Weekday, string> = {
  0: "Monday",
  1: "Tuesday",
  2: "Wednesday",
  3: "Thursday",
  4: "Friday",
  5: "Saturday",
  6: "Sunday",
};

/** "30 min", "2 h", "1 h 30 min". */
export function formatDuration(minutes: number): string {
  const total = Math.max(0, Math.round(minutes));
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  if (hours === 0) return `${total} min`;
  if (rest === 0) return `${hours} h`;
  return `${hours} h ${rest} min`;
}

/** "07:00 to 10:00", or for an anchored window "2 h after waking, 30 min". */
export function formatWindow(window: Window | Anchored): string {
  if (!isAnchored(window)) return `${window.start} to ${window.end}`;
  const anchor = window.anchor === "wake" ? "waking" : "bed";
  const length = formatDuration(window.lengthMin);
  if (window.offsetMin === 0) return `${window.anchor === "wake" ? "On" : "At"} ${anchor}, ${length}`;
  const side = window.offsetMin > 0 ? "after" : "before";
  return `${formatDuration(Math.abs(window.offsetMin))} ${side} ${anchor}, ${length}`;
}

/** "Every day", "Weekdays", "Weekends", or "Mon, Wed, Fri". */
export function formatDays(days: Weekday[]): string {
  const set = new Set(days);
  if (set.size === 7) return "Every day";
  const is = (...wanted: Weekday[]) => set.size === wanted.length && wanted.every((day) => set.has(day));
  if (is(0, 1, 2, 3, 4)) return "Weekdays";
  if (is(5, 6)) return "Weekends";
  if (set.size === 0) return "No days";
  return [...set]
    .sort((a, b) => a - b)
    .map((day) => WEEKDAY_LABELS[day])
    .join(", ");
}

/** "28 on, 14 off" or "Weekly, Monday"; null without a cycle. */
export function formatCycle(cycle: ProtocolItemTemplate["cycle"]): string | null {
  if (!cycle) return null;
  if ("weekly" in cycle) return `Weekly, ${WEEKDAY_NAMES[cycle.weekly]}`;
  return `${cycle.onDays} on, ${cycle.offDays} off`;
}

const VERIFY_NAME: Record<Exclude<Verify, "none">, string> = {
  pen: "the pen",
  vial_syringe: "the vial and syringe",
  pill_bottle: "the pill bottle",
  blister: "the blister",
  powder_tub: "the powder tub",
  tube: "the tube",
  topical: "the topical",
};

/** "Glasses see the pen"; null when the glasses verify nothing. */
export function formatVerify(verify: Verify): string | null {
  if (verify === "none") return null;
  return `Glasses see ${VERIFY_NAME[verify]}`;
}

/** The one 15 muted line under an item's name: window · days · cycle · what the glasses see. */
export function itemMeta(item: ProtocolItemTemplate, { verify = true }: { verify?: boolean } = {}): string {
  return [formatWindow(item.window), formatDays(item.days), formatCycle(item.cycle), verify ? formatVerify(item.verify) : null]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

/** The meaning (a MEANING_ICONS key) an item's kind draws with. */
export function kindMeaning(item: Pick<ProtocolItemTemplate, "kind" | "verify" | "name">): Meaning {
  switch (item.kind) {
    case "dose":
      return item.verify === "pill_bottle" || item.verify === "blister" ? "pill" : "peptide";
    case "meal":
      return "food";
    case "move":
      return "movement";
    case "light":
      return "light";
    case "sleep":
      return /\bnap\b/i.test(item.name) ? "nap" : "sleep";
    case "screens":
      return "screens";
    case "sauna":
      return "sauna";
    case "supplement":
      return "pill";
    case "hydration":
      return /caffeine/i.test(item.name) ? "caffeine" : "water";
  }
}

/** The detail page's sections, in order. Only sections with items render. */
export const SECTIONS: readonly { kind: ItemKind; label: string }[] = [
  { kind: "sleep", label: "Sleep" },
  { kind: "light", label: "Light" },
  { kind: "move", label: "Movement" },
  { kind: "meal", label: "Meals" },
  { kind: "dose", label: "Doses" },
  { kind: "screens", label: "Screens" },
  { kind: "sauna", label: "Sauna" },
  { kind: "supplement", label: "Supplements" },
  { kind: "hydration", label: "Hydration" },
];

/** A template's flags, one line each; empty when none is set. */
export function flagLines(flags: ProtocolTemplate["flags"]): string[] {
  const lines: string[] = [];
  if (flags.nightWakingsNeverRed) lines.push("Night wakings are never red");
  if (flags.napsAnyTimeUnder20) lines.push("Naps any time, under 20 min");
  if (flags.ceilingsLabel) lines.push(`Ceilings ${flags.ceilingsLabel}`);
  if (flags.anchorToActualSleep) lines.push("Every window is measured from when you actually slept");
  return lines;
}

/** The one flag that earns a chip on a library card, if any. */
export function flagChip(flags: ProtocolTemplate["flags"]): string | null {
  if (flags.anchorToActualSleep) return "Windows anchored to sleep";
  if (flags.nightWakingsNeverRed) return "Night wakings never red";
  return null;
}

/** "6 items". */
export const itemCount = (count: number): string => `${count} ${count === 1 ? "item" : "items"}`;
