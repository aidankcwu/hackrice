/**
 * What Protocol shows, from `/api/protocol/today` and nothing else (IOS_SPEC
 * ProtocolView). The backend decides every status; the phone names it.
 */
import type { Family } from "./today";
import type { ProtocolKind, ProtocolStatus, ProtocolTodayItem } from "./types";

/** The four kinds, in the order Add item lists them; each wears its family's symbol. */
export const KINDS: readonly { kind: ProtocolKind; label: string; family: Family }[] = [
  { kind: "dose", label: "Dose", family: "medication" },
  { kind: "meal", label: "Meal", family: "food" },
  { kind: "winddown", label: "Wind‑down", family: "winddown" },
  { kind: "walk", label: "Walk", family: "walk" },
];

export function kindFamily(kind: ProtocolKind): Family {
  return KINDS.find((entry) => entry.kind === kind)?.family ?? "medication";
}

/** "08:42" → "8:42": the spec writes hours without a leading zero. */
function hourMinute(text: string): string {
  return text.replace(/^0(?=\d)/, "");
}

const CLOCK = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });

/** "7:00–10:00", from the backend's local `"HH:MM"` pair. */
export function windowText(item: Pick<ProtocolTodayItem, "window_start" | "window_end">): string {
  return `${hourMinute(item.window_start)}–${hourMinute(item.window_end)}`;
}

/**
 * "Seen 8:42" · "Waiting" · "Missed" · "Done (you)". An item the wearer undid is
 * open again and reads "Waiting" until the backend says otherwise.
 */
export function statusText(item: Pick<ProtocolTodayItem, "status" | "seen_t">): string {
  switch (item.status) {
    case "seen":
      return item.seen_t != null ? `Seen ${hourMinute(CLOCK.format(new Date(item.seen_t * 1000)))}` : "Seen";
    case "done":
      return "Done (you)";
    case "missed":
      return "Missed";
    default:
      return "Waiting";
  }
}

export type RowAction = "done" | "undo" | "delete";

/** The sheet's first choice: Undo on seen or done, Mark done on waiting or missed. Delete is always second. */
export function toggleAction(status: ProtocolStatus): "done" | "undo" {
  return status === "seen" || status === "done" ? "undo" : "done";
}

export const ACTION_LABELS: Record<RowAction, string> = {
  done: "Mark done",
  undo: "Undo",
  delete: "Delete",
};

/** Weekday toggles for Add item, 0 = Monday as the backend counts. */
export const WEEKDAYS: readonly { day: number; letter: string; name: string }[] = [
  { day: 0, letter: "M", name: "Monday" },
  { day: 1, letter: "T", name: "Tuesday" },
  { day: 2, letter: "W", name: "Wednesday" },
  { day: 3, letter: "T", name: "Thursday" },
  { day: 4, letter: "F", name: "Friday" },
  { day: 5, letter: "S", name: "Saturday" },
  { day: 6, letter: "S", name: "Sunday" },
];
