/**
 * Engine payloads (+ the day inputs they came from) → DashboardData.
 * Pure functions only: every string here is derived from the payload, never
 * hardcoded copy about the day.
 */
import { fmtH } from "@/lib/tokens";
import { CAFFEINE_CUTOFF_H, mapEpisodes } from "./adapter";
import { finiteNumber, hhmm, normaliseBedtime, round1, round2, shortWeekday, trimFixed } from "./format";
import type {
  DashboardData,
  DataSource,
  DayInputs,
  EffectRow,
  EngineFactor,
  EnginePayload,
  ForecastView,
  IconName,
  LayerName,
  LayerRow,
  LedgerRow,
  LeverRow,
  Person,
  PinRow,
  WeekDay,
} from "./types";
import { LAYER_ORDER } from "./types";
import { LAYER_DISCOUNT, LEDGER_UNITS } from "./units";

export interface ShapeArgs {
  /** Same order as `days`; the last one is today. */
  payloads: EnginePayload[];
  days: DayInputs[];
  person: Person;
  source: DataSource;
  engineMs: number;
}

// ---------------------------------------------------------------------------
// Layers
// ---------------------------------------------------------------------------

const LAYER_ICONS: Readonly<Record<LayerName, IconName>> = {
  Movement: "footprints",
  Sleep: "moon",
  "Light & clock": "sun",
  Social: "users",
  Environment: "trees",
  "Diet & substances": "wine",
  Recovery: "flame",
};

/**
 * The engine's `_layer_sum`: largest |h| first, each discounted by rank so
 * correlated factors inside a layer are not double counted. Hours are a linear
 * map of the log-hazard the engine sorts on, so the seven layer sums add up to
 * `hours_today` (within the payload's 2-dp rounding). The sort is stable, so
 * ties keep factor-registration order exactly as Python's `sorted` does.
 */
export function layerHours(hours: number[]): number {
  const last = LAYER_DISCOUNT.length - 1;
  return [...hours]
    .sort((a, b) => Math.abs(b) - Math.abs(a))
    .reduce((sum, h, i) => sum + h * LAYER_DISCOUNT[Math.min(i, last)], 0);
}

type DosePhrase = (dose: number) => string;

const DOSE_PHRASES: Readonly<Record<string, DosePhrase>> = {
  steps: (d) => `${Math.round(d).toLocaleString("en-US")} steps`,
  vilpa_min: (d) => `${trimFixed(d, 1)} min hard effort`,
  resistance_min_wk: (d) => `${trimFixed(d, 0)} min strength this week`,
  fitness_pct: (d) => `fitness p${trimFixed(d, 0)}`,
  gait_speed: (d) => `${d.toFixed(2)} m/s`,
  sleep_hours: (d) => `${d.toFixed(1)} h`,
  sri: (d) => `regularity ${trimFixed(d, 0)}`,
  day_light_min: (d) => `${trimFixed(d, 0)} bright min`,
  night_light_lux: (d) => `${trimFixed(d, 1)} lx at night`,
  social_index: (d) => `social ${trimFixed(d, 0)}`,
  purpose: (d) => `purpose ${trimFixed(d, 0)}/6`,
  nature_min_wk: (d) => `${trimFixed(d, 0)} min in nature this week`,
  noise_night_db: (d) => `${trimFixed(d, 0)} dB at night`,
  med_adherence: (d) => `meals ${Math.round(d * 100)}% on pattern`,
  alcohol_drinks: (d) => (d === 0 ? "no drinks" : d === 1 ? "1 drink" : `${trimFixed(d, 1)} drinks`),
  smoker: (d) => (d ? "nicotine daily" : "no nicotine"),
  sauna_wk: (d) => `${trimFixed(d, 1)} sauna session${d === 1 ? "" : "s"}`,
  recovery_ratio: (d) => `HRV ${d.toFixed(2)}× baseline`,
};

/** One line of measured doses, e.g. "9,100 steps · 2 min hard effort". */
export function doseNote(factors: EngineFactor[]): string {
  const parts: string[] = [];
  for (const f of factors) {
    if (f.dose === null) continue;
    const phrase = DOSE_PHRASES[f.key];
    // A factor the engine grew after this file was written still shows its number.
    parts.push(phrase ? phrase(f.dose) : `${f.label.toLowerCase()} ${trimFixed(f.dose, 2)}`);
  }
  return parts.join(" · ");
}

export function layerRows(payload: EnginePayload): LayerRow[] {
  return LAYER_ORDER.map((name) => {
    const factors = payload.factors.filter((f) => f.layer === name);
    const measured = factors.filter((f) => f.measured);
    return {
      name,
      icon: LAYER_ICONS[name],
      // A layer the engine did not score is 0 with `measured: 0`, which the UI
      // reads as "nothing here" — it is never presented as a measured score.
      score: Math.round(payload.layers[name] ?? 0),
      hours: round2(layerHours(measured.map((f) => f.hours))),
      note: measured.length > 0 ? doseNote(measured) : "nothing measured today",
      measured: measured.length,
      total: factors.length,
    };
  });
}

// ---------------------------------------------------------------------------
// Pins
// ---------------------------------------------------------------------------

/** `seen` prefixes the engine writes in `pins_from_episodes` → icon. */
const PIN_ICONS: ReadonlyArray<[prefix: string, icon: IconName]> = [
  ["Outdoors", "sun"],
  ["Conversation", "users"],
  ["Alcohol", "wine"],
  ["Caffeine", "coffee"],
  ["Screen", "smartphone"],
  ["Meal", "utensils"],
  ["Sauna", "flame"],
  ["Walking", "footprints"],
];

const pinIcon = (seen: string): IconName => PIN_ICONS.find(([prefix]) => seen.startsWith(prefix))?.[1] ?? "eye";

export function pinRows(payload: EnginePayload): PinRow[] {
  return payload.pins.map((pin, i) => ({
    id: `${pin.time}-${i}`,
    time: pin.time,
    seen: pin.seen,
    kind: pin.kind === "credit" ? "earn" : "cost",
    effect: pin.effect,
    grade: pin.grade,
    img: pin.img,
    icon: pinIcon(pin.seen),
  }));
}

// ---------------------------------------------------------------------------
// Tonight
// ---------------------------------------------------------------------------

/** Engine `LEADING["night_screen_min"].sleep_h_per_60min` — −0.25 h of sleep per hour of screens after 22:00. */
const SLEEP_H_PER_SCREEN_HOUR = 0.25;

/**
 * `fix` is written from the drivers the engine listed and nothing else. The
 * driver strings are the engine's (`forecast_tonight`): "caffeine at …",
 * "… min of screens after 22:00", "… drink(s) — …", "bedtime … min vs habit".
 *
 * `bedtimeMeasured` is false when no `bed_time` row exists anywhere in the week:
 * the engine still runs on its 23:00 default, but a default is not Bryan's habit,
 * so neither the `bedtime` field nor any sentence may quote it as one (R1).
 */
export function forecastView(payload: EnginePayload, bedtime_hh: number, bedtimeMeasured: boolean): ForecastView {
  const forecast = payload.forecast;
  const drivers = forecast.drivers;
  const bedtime = bedtimeMeasured ? hhmm(bedtime_hh) : "—";
  const caffeine = drivers.some((d) => d.startsWith("caffeine"));
  const alcohol = drivers.some((d) => d.includes("drink"));
  // Only emitted when the engine saw `planned_bed_shift_min`, which the adapter
  // sets only from a measured `bed_time` — so a shift driver implies a real habit.
  const shift = drivers.some((d) => d.startsWith("bedtime"));
  const screens = payload.observations.night_screen_min ?? 0;

  const sentences: string[] = [];
  if (screens > 0) {
    sentences.push(`No more screens tonight recovers about ${round1((SLEEP_H_PER_SCREEN_HOUR * screens) / 60)} h of sleep.`);
  }
  if (caffeine && alcohol) sentences.push("The coffee and the drinks are already booked.");
  else if (caffeine) sentences.push("The coffee is already booked.");
  else if (alcohol) sentences.push("The drinks are already booked.");
  if (shift) sentences.push(`Lights out at ${hhmm(bedtime_hh)} keeps your regularity score.`);
  if (drivers.length === 0) {
    sentences.push(
      bedtimeMeasured
        ? `Nothing is dragging tonight down. Lights out near ${bedtime} keeps it that way.`
        : "Nothing measured today is dragging tonight down.",
    );
  }
  return { ...forecast, bedtime, fix: sentences.join(" ") };
}

// ---------------------------------------------------------------------------
// Levers, ledger, effects
// ---------------------------------------------------------------------------

export function leverRows(payload: EnginePayload): LeverRow[] {
  return payload.levers.map((l) => ({
    key: l.key,
    action: l.action,
    gain: l.hours_gain,
    time: l.time_min,
    layers: l.layers,
    source: l.source,
  }));
}

export function ledgerRows(payload: EnginePayload): LedgerRow[] {
  return payload.ledger.map((line) => ({ ...line, unit: LEDGER_UNITS[line.key] ?? "" }));
}

const signed = (x: number, text: string): string => `${x < 0 ? "−" : "+"}${text}`;

export function effectRows(payload: EnginePayload): EffectRow[] {
  return payload.effects.map((e) => {
    if (e.beta === null) {
      return {
        exposure: e.exposure,
        outcome: e.outcome,
        beta: "fewer than 14 days — population prior shown",
        lo: null,
        hi: null,
        n: e.n,
        ok: false,
        note: e.note,
      };
    }
    const magnitude = Math.abs(e.beta);
    // ln-outcome per minute of exposure reads best as a percent change per 10 min.
    const beta =
      e.outcome.includes("ln") && e.exposure.includes("min")
        ? signed(e.beta, `${(magnitude * 10 * 100).toFixed(1)}% per 10 min`)
        : signed(e.beta, `${magnitude.toFixed(3)} per unit`);
    return {
      exposure: e.exposure,
      outcome: e.outcome,
      beta,
      lo: e.ci[0],
      hi: e.ci[1],
      n: e.n,
      ok: e.n >= 14 && e.note.includes("personal"),
      note: e.note,
    };
  });
}

// ---------------------------------------------------------------------------
// Week
// ---------------------------------------------------------------------------

const orNull = (value: unknown): number | null => finiteNumber(value) ?? null;

/** `habitualBed` is decimal hours (may exceed 24); caffeine later than the engine's default 9 h before it is "late". */
export function weekDays(payloads: EnginePayload[], days: DayInputs[], habitualBed: number): WeekDay[] {
  const cutoffHh = habitualBed - CAFFEINE_CUTOFF_H;
  return days.map((day, i) => {
    const s = day.seeded;
    const episodes = mapEpisodes(day);
    const bed = finiteNumber(s.bed_time);

    const tags: string[] = [];
    if (s.journal_caffeine_late === 1 || episodes.some((e) => e.type === "caffeine_sighting" && e.start_hh > cutoffHh)) {
      tags.push("caffeine");
    }
    if (s.journal_alcohol === 1 || episodes.some((e) => e.type === "alcohol_sighting")) tags.push("alcohol");
    if (s.journal_nicotine === 1) tags.push("nicotine");
    if (s.journal_cannabis === 1) tags.push("cannabis");

    return {
      day: shortWeekday(day.date),
      date: day.date,
      bed: bed === undefined ? "—" : hhmm(normaliseBedtime(bed)),
      sleep: orNull(s.sleep_hours),
      hrv: orNull(s.hrv_rmssd_ratio),
      rec: orNull(s.recovery_score),
      rhr: orNull(s.resting_hr),
      steps: orNull(s.steps),
      sri: orNull(s.sleep_regularity_sri),
      hours: payloads[i]?.hours_today ?? null,
      tag: [...new Set(tags)].join(", "),
      today: day.isToday,
    };
  });
}

/**
 * Mean over the days that carry the number, with the count that went into it.
 * The count is what the sentence may claim: averaging three nights of sleep and
 * calling it five days would be an invented fact (R1).
 */
const average = (values: Array<number | null>): { mean: number; n: number } | undefined => {
  const present = values.filter((v): v is number => v !== null);
  if (present.length === 0) return undefined;
  return { mean: present.reduce((a, b) => a + b, 0) / present.length, n: present.length };
};

/** One sentence under the seven-day table, computed from the rows and nothing else. */
export function weekSummary(week: WeekDay[]): string {
  const scored = week.filter((d): d is WeekDay & { hours: number } => d.hours !== null);
  const worst = scored.reduce<(WeekDay & { hours: number }) | undefined>(
    (acc, d) => (acc === undefined || d.hours < acc.hours ? d : acc),
    undefined,
  );
  const best = scored.reduce<(WeekDay & { hours: number }) | undefined>(
    (acc, d) => (acc === undefined || d.hours > acc.hours ? d : acc),
    undefined,
  );

  const late = week.filter((d) => d.tag.includes("caffeine"));
  const rest = week.filter((d) => !d.tag.includes("caffeine"));
  const lateSleep = average(late.map((d) => d.sleep));
  const restSleep = average(rest.map((d) => d.sleep));

  if (lateSleep !== undefined && restSleep !== undefined) {
    const lateRec = average(late.map((d) => d.rec));
    const restRec = average(rest.map((d) => d.rec));
    // Every count is the number of days that actually carried the metric, and the
    // recovery count is named separately when fewer of those nights reported it.
    const recText = (rec: { mean: number; n: number }, sleepN: number, label: string): string =>
      ` and ${label}${Math.round(rec.mean)}${rec.n === sleepN ? "" : ` over ${rec.n} of them`}`;
    const lateText =
      `${lateSleep.n} late-caffeine day${lateSleep.n === 1 ? "" : "s"} averaged ${lateSleep.mean.toFixed(1)} h of sleep` +
      (lateRec === undefined ? "" : recText(lateRec, lateSleep.n, "recovery "));
    const restText =
      `${restSleep.n} without late caffeine averaged ${restSleep.mean.toFixed(1)} h` +
      (restRec === undefined ? "" : recText(restRec, restSleep.n, ""));
    const worstText = worst === undefined ? "" : ` ${worst.day} was the worst: ${fmtH(worst.hours)}.`;
    return `${lateText}; ${restText}.${worstText}`;
  }
  if (worst === undefined || best === undefined) return "No day has been scored yet.";
  if (worst === best) return `${worst.day} is the only scored day, at ${fmtH(worst.hours)}.`;
  return `${worst.day} was the worst day at ${fmtH(worst.hours)}; ${best.day} the best at ${fmtH(best.hours)}.`;
}

// ---------------------------------------------------------------------------
// Assembly
// ---------------------------------------------------------------------------

export function shapeDashboard({ payloads, days, person, source, engineMs }: ShapeArgs): DashboardData {
  const today = payloads[payloads.length - 1];
  const todayInputs = days[days.length - 1];
  if (today === undefined || todayInputs === undefined) {
    throw new Error("shapeDashboard: at least one payload and one day are required");
  }
  // The profile the engine actually ran with is the bedtime the UI reasons about.
  const week = weekDays(payloads, days, person.bedtime_hh);
  // `person.bedtime_hh` is the engine's 23:00 default when no night was recorded.
  const bedtimeMeasured = days.some((d) => finiteNumber(d.seeded.bed_time) !== undefined);
  return {
    generated_at: todayInputs.nowT,
    source,
    person,
    overall: today.overall,
    hours_today: today.hours_today,
    hours_ci: today.hours_ci,
    years_delta: today.years_delta,
    years_ci: today.years_ci,
    layers: layerRows(today),
    pins: pinRows(today),
    forecast: forecastView(today, person.bedtime_hh, bedtimeMeasured),
    levers: leverRows(today),
    ledger: ledgerRows(today),
    effects: effectRows(today),
    week,
    week_summary: weekSummary(week),
    factors: today.factors,
    insights: today.insights,
    observations: today.observations,
    engine_ms: engineMs,
  };
}
