/**
 * Pipeline store → engine request. Pure functions only (no I/O, no React).
 *
 * Data honesty (R1): nothing here is invented. A field the pipeline does not
 * measure is left undefined so the engine imputes the population reference and
 * credits the factor 0 hours. Where a measured number stands in for a different
 * one the comment says "Proxy:". The one optimistic assumption — every gym
 * minute counted as strength — is flagged where it happens, and it is the only
 * one.
 */
import { compact, finiteNumber, localDecimalHour, mean, median, normaliseBedtime, round1, round2 } from "./format";
import type {
  DayInputs,
  EngineEpisode,
  EngineEpisodeType,
  EngineHistory,
  EngineProfile,
  EngineRequest,
  EngineWearableDay,
  EngineWeekRow,
  EngineWorkout,
  PipelineEpisode,
} from "./types";

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

/** The engine's `observations_from_app` nature set, after aliasing. */
export const NATURE_SCENES: ReadonlySet<string> = new Set(["park", "trees", "trail", "water", "garden", "nature"]);

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

/** Sessions shorter than this are not the Laukkanen dose (>19 min) the sauna factor is built on. */
const SAUNA_MIN_MINUTES = 19;

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
 * rows. With no row it returns the engine's own `DEFAULT_BEDTIME_H` of 23 —
 * the caffeine cutoff needs *some* anchor — but that is a default, not a
 * measurement, so `shapeDashboard` renders the bedtime as "—" until a real
 * night exists rather than quoting 23:00 back as Bryan's habit.
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

/**
 * The pipeline's `gym_session` does not separate strength from cardio and the
 * engine only credits strength, so every gym minute is booked as strength.
 * This is the single optimistic assumption in the adapter.
 */
function strengthWorkouts(engineEpisodes: EngineEpisode[]): EngineWorkout[] {
  return engineEpisodes
    .filter((e) => e.type === "gym_session")
    .map((e) => ({ kind: "strength", minutes: e.minutes }));
}

const minutesOf = (episodes: EngineEpisode[], type: EngineEpisodeType, pred: (e: EngineEpisode) => boolean = () => true): number =>
  episodes.filter((e) => e.type === type && pred(e)).reduce((sum, e) => sum + e.minutes, 0);

/**
 * `week` is the days up to and including `day`, oldest first — the habit that
 * tonight's bedtime and baseline sleep are measured against.
 *
 * Deliberately never set, because nothing in the pipeline or on the wearable
 * measures them — the engine imputes each at its population reference and
 * credits 0 hours, which is the honest answer (R1):
 *
 *   - `night_lux`   — the tick's `lux_proxy` is relative luminance from an
 *                     auto-exposed JPEG, not lux at the eye.
 *   - `vo2max_pct`  — the seed carries a `vo2_max` lookalike, but no device in
 *                     this build estimates VO2max, and a percentile needs a
 *                     reference population the backend does not have.
 *   - `purpose`     — a questionnaire score; nobody is asked. The seeded
 *                     `purpose_score` row is invented, so it is ignored.
 *   - `height_m`    — never collected.
 *   - `smoker`      — not observed. Leaving it unset lets the engine use its own
 *                     0 reference rather than this adapter asserting "non-smoker".
 *
 * Everything that *is* set is a straight rename of a stored metric, except
 * `workouts` (see `strengthWorkouts`).
 */
export function buildWearableDay(day: DayInputs, engineEpisodes: EngineEpisode[], week: DayInputs[]): EngineWearableDay {
  const s = day.seeded;
  const weekSleep: number[] = [];
  for (const d of week) {
    const h = finiteNumber(d.seeded.sleep_hours);
    if (h !== undefined) weekSleep.push(h);
  }
  const bed = finiteNumber(s.bed_time);
  const bedShift =
    bed === undefined ? undefined : Math.round((normaliseBedtime(bed) - habitualBedtime(week.map((d) => d.seeded))) * 60);

  // `compact` drops every undefined, so an absent row never reaches the engine as a number.
  return compact<EngineWearableDay>({
    steps: finiteNumber(s.steps),
    vilpa_min: finiteNumber(s.vilpa_minutes),
    sleep_hours: finiteNumber(s.sleep_hours),
    sri: finiteNumber(s.sleep_regularity_sri),
    hrv_ratio: finiteNumber(s.hrv_rmssd_ratio),
    strain: finiteNumber(s.strain),
    night_db: finiteNumber(s.night_noise_db),
    workouts: strengthWorkouts(engineEpisodes),
    // Proxy: the mean of the nights actually recorded in this window, not a
    // 60-day habit. `mean` of nothing is undefined, so a week with no sleep row
    // leaves the forecast's baseline to the engine.
    baseline_sleep_h: mean(weekSleep),
    planned_bed_shift_min: bedShift,
  });
}

/** Same formula as the engine's `observations_from_app`: 60 % contact minutes (saturates at 60), 40 % breadth (saturates at 5 people). */
export function socialIndex(conversationMin: number, people: number): number {
  return 100 * ((0.6 * Math.min(conversationMin, 60)) / 60 + (0.4 * Math.min(people, 5)) / 5);
}

export function buildWeekRow(day: DayInputs, engineEpisodes: EngineEpisode[]): EngineWeekRow {
  const s = day.seeded;
  const conversations = engineEpisodes.filter((e) => e.type === "conversation");
  const convMin = conversations.reduce((sum, e) => sum + e.minutes, 0);
  const people = conversations.reduce((max, e) => Math.max(max, e.people ?? 0), 0);

  // Episode-derived minutes are observations, so zero here means "the glasses
  // were on and saw none" — a real zero, not a stand-in. `social_index` is the
  // exception: with no conversation episode nothing was observed either way, so
  // it is left undefined rather than reported as a social score of 0.
  return compact<EngineWeekRow>({
    nature_min: minutesOf(engineEpisodes, "outdoor_block", (e) => NATURE_SCENES.has(e.scene ?? "")),
    steps: finiteNumber(s.steps),
    // Proxy: minutes outdoors, not minutes above a melanopic-lux threshold.
    day_light_min: minutesOf(engineEpisodes, "outdoor_block"),
    vilpa_min: finiteNumber(s.vilpa_minutes),
    social_index: conversations.length > 0 ? socialIndex(convMin, people) : undefined,
    workouts: strengthWorkouts(engineEpisodes),
    sauna: engineEpisodes.some((e) => e.type === "sauna" && e.minutes > SAUNA_MIN_MINUTES) ? 1 : 0,
  });
}

/**
 * Outdoor minutes on day D against ln(HRV ratio) of the night that starts on
 * day D (the seed's day/night convention), with strain as the covariate when
 * every usable day has it. Undefined below two usable days; the engine itself
 * reports "fewer than 14 days" until there is real history.
 */
export function buildHistory(days: DayInputs[]): EngineHistory | undefined {
  const usable: Array<{ day: DayInputs; ratio: number }> = [];
  for (const day of days) {
    const ratio = finiteNumber(day.seeded.hrv_rmssd_ratio);
    // ln() needs a positive ratio; a zero or negative row is a broken feed, not a night.
    if (ratio !== undefined && ratio > 0) usable.push({ day, ratio });
  }
  if (usable.length < 2) return undefined;

  const strains = usable.map(({ day }) => finiteNumber(day.seeded.strain));
  const covariates = strains.every((v): v is number => v !== undefined) ? strains : undefined;

  return compact<EngineHistory>({
    exposure: usable.map(({ day }) => minutesOf(mapEpisodes(day), "outdoor_block")),
    outcome: usable.map(({ ratio }) => Math.log(ratio)),
    covariates,
    weekday: usable.map(({ day }) => new Date(`${day.date}T12:00:00`).getDay()),
    exposure_name: "outdoor minutes",
    outcome_name: "next-night ln HRV ratio",
    prior_beta: 0.002,
    prior_se: 0.003,
  });
}

/**
 * One engine request per day, oldest first (the order `days` arrives in — the
 * loader builds them that way, and `shapeDashboard` relies on payload i
 * matching day i). The last element is the scored day and the only one that
 * carries `history`; earlier days use the days before them as `week_rows`.
 */
export function buildDayRequests(days: DayInputs[], profile: EngineProfile): EngineRequest[] {
  const mapped = days.map(mapEpisodes);
  const fullProfile: EngineProfile = {
    ...profile,
    bedtime_hh: profile.bedtime_hh ?? habitualBedtime(days.map((d) => d.seeded)),
  };
  const history = buildHistory(days);

  return days.map((day, i) => {
    const episodes = mapped[i].map((e) => ({ ...e, frame_url: day.frameUrls[e.id ?? ""] ?? null }));
    const request: EngineRequest = {
      profile: fullProfile,
      episodes,
      wearable_day: buildWearableDay(day, mapped[i], days.slice(0, i + 1)),
      week_rows: days.slice(0, i).map((d, j) => buildWeekRow(d, mapped[j])),
    };
    if (i === days.length - 1 && history) request.history = history;
    return request;
  });
}
