/**
 * Pipeline episodes → the engine's episode vocabulary, and the habitual
 * bedtime, for the seven-day table's tags (shape.ts `weekDays`) and the
 * adherence log (adherence-evidence.ts). Pure functions only (no I/O, no React).
 *
 * No score is built here any more: the dashboard's numbers are the backend's
 * `/api/healthspan` (loader.ts), which applies its own coverage rule.
 *
 * Data honesty (R1): nothing here is invented. A field the pipeline does not
 * measure is left undefined.
 */
import { finiteNumber, localDecimalHour, median, normaliseBedtime, round1, round2 } from "./format";
import type { DayInputs, EngineEpisode, EngineEpisodeType, PipelineEpisode } from "./types";

/** Pipeline `kind` → engine `type`. Kinds not listed carry nothing the engine scores. */
const KIND_TO_TYPE: Readonly<Record<string, EngineEpisodeType>> = {
  screen_block: "screen_block",
  caffeine_sighting: "caffeine_sighting",
  meal: "meal",
  conversation: "conversation",
  outdoor_block: "outdoor_block",
  alcohol_sighting: "alcohol_sighting",
  sauna_session: "sauna",
  gym_session: "gym_session",
};

/** T0 scene values that the engine's nature vocabulary spells differently. */
const SCENE_ALIASES: Readonly<Record<string, string>> = { beach: "water", backyard: "garden" };

/**
 * backend/pipeline/models.py `HEALTHY_FOOD_TYPES`, verbatim — what the pipeline
 * itself calls on-pattern. Nothing outside this set is tagged `mediterranean`:
 * the tag is what the engine's `med_adherence` counts, so widening it by guess
 * would invent diet adherence.
 */
const HEALTHY_FOOD_TYPES: ReadonlySet<string> = new Set([
  "vegetables", "fruit", "grains", "beans_legumes", "fish", "seafood",
  "poultry", "salad", "rice_bowl", "soup", "eggs", "nuts", "mixed",
]);

/** Default caffeine cutoff, hours before bed (engine `caffeine_cutoff_h_before_bed` for a normal metaboliser). */
export const CAFFEINE_CUTOFF_H = 9;

function mapScene(raw: unknown): string | undefined {
  if (raw === undefined || raw === null) return undefined;
  const scene = String(raw);
  return SCENE_ALIASES[scene] ?? scene;
}

/** One pipeline episode → engine vocabulary, or null when the engine has no use for it. */
export function episodeToEngine(episode: PipelineEpisode, ctx: { nowT: number }): EngineEpisode | null {
  const type = KIND_TO_TYPE[episode.kind];
  if (type === undefined) return null;

  const rawMinutes = episode.open ? Math.max(0, (ctx.nowT - episode.start_t) / 60) : episode.duration_s / 60;
  const out: EngineEpisode = {
    type,
    start_hh: localDecimalHour(episode.start_t),
    minutes: round1(rawMinutes),
    id: episode.id,
    kind: episode.kind,
  };
  // `lux` is deliberately never set: the tick's lux_proxy is relative luminance
  // from an auto-exposed JPEG, not lux at the eye.
  const scene = mapScene(episode.dominant.scene);
  if (scene !== undefined) out.scene = scene;

  if (type === "conversation") {
    // `people_present` is the trigger, so a conversation implies at least one
    // other person; the count itself is not measured (no diarization), hence 1.
    out.people = 1;
  } else if (type === "meal") {
    // An untyped meal is labelled "meal" and carries no food tag: the engine reads
    // `tags` for diet adherence, and "a meal happened" says nothing about pattern.
    const food = episode.dominant.food_type === undefined ? undefined : String(episode.dominant.food_type);
    out.label = food ?? "meal";
    if (food !== undefined) out.tags = HEALTHY_FOOD_TYPES.has(food) ? [food, "mediterranean"] : [food];
  } else if (type === "alcohol_sighting") {
    // One sighting = one drink; the glasses do not count pours.
    out.count = 1;
  }
  return out;
}

/** Every engine-relevant episode of a day, open ones closed at the day's `nowT`. */
export function mapEpisodes(day: DayInputs): EngineEpisode[] {
  const out: EngineEpisode[] = [];
  for (const episode of day.episodes) {
    const mapped = episodeToEngine(episode, { nowT: day.nowT });
    if (mapped) out.push(mapped);
  }
  return out;
}

/**
 * Habitual bedtime (decimal hours, may exceed 24) from the week's `bed_time`
 * rows, for the adherence log's "bed in window" check. With no row it returns
 * the engine's own `DEFAULT_BEDTIME_H` of 23 — the check needs *some* anchor —
 * but that is a default, not a measurement.
 */
export function habitualBedtime(seededDays: Record<string, number>[]): number {
  const bedtimes: number[] = [];
  for (const seeded of seededDays) {
    const bed = finiteNumber(seeded.bed_time);
    if (bed !== undefined) bedtimes.push(normaliseBedtime(bed));
  }
  const med = median(bedtimes);
  return med === undefined ? 23 : round2(med);
}
