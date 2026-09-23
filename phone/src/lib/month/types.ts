/**
 * A month of lived days, as the glasses, the watch and the phone would report
 * them. Times are minutes after local midnight (Houston). Every record carries
 * `seeded: true` while the month comes from `fixtures/month/days.json`.
 *
 * Imported with a `.ts` extension by `fixtures/month/generate.ts`, which Node
 * runs directly, so this file holds types only.
 */

export type DayType =
  | "clean"
  | "perfect"
  | "late_caffeine"
  | "drinking_night"
  | "skipped_lunch"
  | "late_dinner"
  | "late_workout"
  | "late_nap"
  | "screens_in_bed"
  | "travel"
  | "crying_baby"
  | "sick"
  | "social_evening";

export type FoodClass = "whole" | "fast_food" | "sweets" | "ultra_processed";

interface Base {
  id: string;
  /** Minutes after local midnight. */
  start: number;
  seeded: true;
}

interface Span extends Base {
  minutes: number;
}

export type MonthEvent =
  | (Base & { kind: "caffeine"; drink: "coffee" | "tea" | "energy_drink" })
  | (Base & { kind: "meal"; label: string; food: FoodClass; thumb: string })
  | (Base & { kind: "skipped_meal"; meal: "breakfast" | "lunch" | "dinner" })
  | (Base & { kind: "alcohol"; drinks: number; label: string })
  | (Base & { kind: "nicotine"; label: string })
  | (Span & { kind: "workout"; label: string; vigorous: boolean })
  | (Span & { kind: "outdoor"; label: string; sunlight: boolean })
  | (Span & { kind: "sedentary" })
  | (Span & { kind: "screen"; device: "computer" | "phone" })
  | (Span & { kind: "phone_in_bed" })
  | (Span & { kind: "conversation"; label: string })
  | (Span & { kind: "nap" })
  | (Span & { kind: "sauna" })
  | (Span & { kind: "cold" })
  | (Base & { kind: "water"; ml: number })
  | (Base & { kind: "peptide"; dose: "AM" | "PM"; taken: boolean; thumb: string | null })
  | (Base & { kind: "supplements"; label: string })
  | (Span & { kind: "drive"; label: string })
  | (Span & { kind: "work"; label: string })
  | (Base & { kind: "stress"; scene: string; hr: number; resting: number })
  | (Base & {
      kind: "mind_check";
      ms: number;
      lapses: number;
      energy: number | null;
      mood: number | null;
      clarity: number | null;
    })
  | (Base & { kind: "whispered"; line: string })
  | (Base & { kind: "asked"; line: string })
  | (Base & { kind: "acted"; line: string });

export type EventKind = MonthEvent["kind"];

/** A MonthEvent before it gets its id and seeded flag. Distributive, so each kind keeps its own fields. */
export type EventDraft = MonthEvent extends infer E ? (E extends MonthEvent ? Omit<E, "id" | "seeded"> : never) : never;

export interface NightWaking {
  /** Minutes after the midnight of the day this sleep ends on. */
  start: number;
  minutes: number;
  /** The baby woke; not the sleeper's decision, never drawn red. */
  baby: boolean;
}

/** The night that ends on this day's morning. `bed` may be negative (before midnight). */
export interface Sleep {
  bed: number;
  wake: number;
  minutes: number;
  deep: number;
  rem: number;
  fragmented: boolean;
  wakings: NightWaking[];
  seeded: true;
}

export interface Day {
  /** ISO date, local. */
  date: string;
  type: DayType;
  /** Last night: the sleep that ended this morning. */
  sleep: Sleep;
  events: MonthEvent[];
  /** Air quality index for the day (US AQI). */
  aqi: number;
  weather: { high_f: number; summary: string };
  /** Decisions the system chose not to speak. */
  held_back: number;
  /** Minutes after midnight the record stops at; 1440 for a finished day. */
  until: number;
  seeded: true;
}

export interface Month {
  city: string;
  lat: number;
  lon: number;
  /** Offset from UTC in hours for the month (CDT). */
  utc_offset: number;
  days: Day[];
  seeded: true;
}
