/**
 * Day inputs (episodes + seeded wearable rows) → the evidence the adherence
 * log settles against. Same source data as the score request, so "done"
 * means the glasses or WHOOP actually saw it.
 */
import { habitualBedtime, mapEpisodes } from "./score/adapter";
import { finiteNumber, normaliseBedtime } from "./score/format";
import type { DayInputs } from "./score/types";
import type { DayEvidence } from "./store/adherence";

/** WHOOP day strain at or above this is a "strenuous" day — a spike for the vigorous-burst lever when no VILPA minutes are logged. */
export const STRAIN_SPIKE = 14;
/** Minutes of vigorous bursts that count as doing the lever (the engine's lever step). */
export const VILPA_DONE_MIN = 3;
/** Bedtime within this many minutes of habit keeps the regularity score. */
export const BED_WINDOW_MIN = 30;

export function dayEvidence(days: DayInputs[]): DayEvidence[] {
  return days.map((day, i) => {
    const episodes = mapEpisodes(day);
    const outdoor = episodes.filter((e) => e.type === "outdoor_block").reduce((sum, e) => sum + e.minutes, 0);
    const strength = episodes.filter((e) => e.type === "gym_session").reduce((sum, e) => sum + e.minutes, 0);
    const vilpa = finiteNumber(day.seeded.vilpa_minutes) ?? 0;
    const strain = finiteNumber(day.seeded.strain) ?? 0;
    const bed = finiteNumber(day.seeded.bed_time);
    const habit = habitualBedtime(days.slice(0, i + 1).map((d) => d.seeded));
    return {
      date: day.date,
      outdoor_min: outdoor,
      vigorous: vilpa >= VILPA_DONE_MIN || strain >= STRAIN_SPIKE,
      strength_min: strength,
      bed_in_window: bed === undefined ? null : Math.abs(normaliseBedtime(bed) - habit) * 60 <= BED_WINDOW_MIN,
    };
  });
}
