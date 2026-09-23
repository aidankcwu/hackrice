/**
 * The two ceilings, calibrated. For any day, Cognition and Body are the percent
 * of the wearer's own 100 they operated at: 100 times the product of (1 - cost)
 * over every term active that day. "Your 100" is the best 7-day rolling median
 * of the morning PVT-B and of overnight HRV; until seven days hold both, the
 * ceilings say "calibrating".
 *
 * Cognition terms: sleep debt on Van Dongen's curve, last night's drinks
 * (Gunn), room CO2 while indoors (Allen), water by 18:00, a long nap's inertia
 * (Brooks & Lack), a night broken by the baby (Insana), a sick day, and the
 * rest of the rules at their own effect. Rules that work through tonight's
 * sleep (caffeine, a late workout, screens, the phone in bed, a nap after
 * 16:00) carry no direct cost once the night is recorded: the observed night
 * already holds their loss, so the debt line names them and today's forecast
 * of tonight's loss becomes the lever.
 *
 * Body terms: HRV and resting HR against the wearer's baseline (attributed to
 * drinks, the baby or a sick day when they explain the night), sleep debt, recovery from a
 * hard workout, water, and the rules with a body effect. Every converted number
 * is labelled "assumed" in the contribution's line or the rule's own text.
 */
import type { Day, MonthEvent } from "./month/types";
import { clock, evaluateDay, hm, RULES, sleepCause, waterMl, WINDOWS, type Finding, type RuleId } from "./rules";

export interface Contribution {
  finding: Finding;
  /** 0..1: how much of the finding's effect is still active on this day. */
  weight: number;
  /** Points of ceiling lost on this day. */
  cognition: number;
  body: number;
}

export interface Calibration {
  /** Days so far with both a morning PVT and an overnight HRV, capped at 7. */
  days: number;
  ready: boolean;
  /** The lowest 7-day rolling median of morning PVT-B, ms; null until ready. */
  pvtBestMs: number | null;
  /** The highest 7-day rolling median of overnight HRV, ms; null until ready. */
  hrvBestMs: number | null;
  /** "calibrating, 4 of 7 days", then "your 100%: PVT 296 ms, HRV 58 ms". */
  label: string;
}

export interface Measured {
  /** This morning's PVT against the best median: best / today x 100, capped at 100. */
  cognition: number | null;
  /** Last night's HRV against the best median: today / best x 100, capped at 100. */
  body: number | null;
}

export interface Inputs {
  sleepDebtHours: number;
  drinksLastNight: number;
  /** Minutes of tonight's sleep today's broken rules are forecast to cost. */
  sleepLossForecastMin: number;
  co2Minutes: { amber: number; red: number };
  waterMl: number;
  /** The longest nap over 30 min today, minutes; 0 when none. */
  napInertiaMin: number;
  postpartum: boolean;
  sick: boolean;
  hrvMs: number | null;
  rhrBpm: number | null;
  hardExerciseYesterday: boolean;
}

export interface Operating {
  date: string;
  cognition: number;
  body: number;
  /** Active costs on this day, biggest first. */
  contributions: Contribution[];
  /** The two biggest, in plain words. */
  top: string[];
  /** The one change that would lift tomorrow most. */
  lever: { rule: RuleId; text: string } | null;
  calibration: Calibration;
  measured: Measured;
  inputs: Inputs;
}

/** Fractions of the ceiling, each with the study and the conversion behind it. */
const COST = {
  /** Van Dongen's 14 nights at 6 h (28 h of debt) match 1 to 2 nights without sleep; Lim & Dinges' g of about 0.7. */
  debtPerHour: 0.005,
  debtCap: 0.2,
  debtBodyPerHour: 0.003,
  /** Gunn 2018, next morning at 0.00 BAC: attention g=0.47, speed g=0.66. */
  perDrink: 0.02,
  drinksCap: 0.1,
  drinksResidual: 0.5,
  /** Allen 2016: -15% at 945 ppm, -50% at 1,400 ppm; the red band held at 30% as a floor of that range. */
  co2Amber: 0.15,
  co2Red: 0.3,
  /** Armstrong 2012 and Ganio 2011: 1.4 to 1.6% body-water loss. */
  hydration: 0.02,
  hydrationBody: 0.01,
  /** Brooks & Lack: 41% worse on waking for 35 to 95 min after a nap over 30 min, averaged over a 16 h day. */
  napInertia: (0.41 * 65) / 960,
  /** Insana 2013: slowest reaction times stay worse across the postpartum weeks. */
  postpartum: 0.03,
  postpartumNext: 0.015,
  postpartumBody: 0.015,
  /** A protocol default: a sick day is a recovery day. */
  sick: 0.15,
  sickBody: 0.25,
  /** Half the relative HRV drop, 1% per bpm of resting HR above baseline. */
  hrvShare: 0.5,
  hrvCap: 0.15,
  rhrPerBpm: 0.01,
  rhrCap: 0.08,
  /** A vigorous workout of 45 min or more costs the next day's body. */
  hardExercise: 0.03,
  hardExerciseMinutes: 45,
} as const;

/** Grosicki 2026, per drink that night: HRV -3.3 to -3.8 ms, resting HR +2.4 to +2.8 bpm; midpoints, assumed. */
const HRV_PER_DRINK = 3.5;
const RHR_PER_DRINK = 2.6;

const WAKING_MINUTES = 16 * 60;
const FULL_NIGHT = 8 * 60;
const SHORT_NIGHT = 7 * 60;
const DEBT_NIGHTS = 14;
const NOON = 12 * 60;
const CALIBRATION_DAYS = 7;
/** A rolling median counts once the 7-day window spans 7 days and holds at least 4 readings. */
const MIN_WINDOW_READINGS = 4;
/** Hours of debt past which the cognition cost is capped; the body term stops there too. */
const DEBT_CAP_HOURS = COST.debtCap / COST.debtPerHour;
const LOOKBACK = 4; // no rule decays over more than 3 days

const DEBT_ASSUMED = `${pct(COST.debtPerHour)} of cognition and ${pct(COST.debtBodyPerHour)} of body per hour of debt, assumed: Van Dongen's 14 nights at 6 h match 1 to 2 nights without sleep, Lim & Dinges' g of about 0.7.`;
const BASELINE_ASSUMED = `Half the relative HRV drop, capped at ${pct(COST.hrvCap)}, and ${pct(COST.rhrPerBpm)} of body per bpm above baseline, capped at ${pct(COST.rhrCap)}, assumed.`;

/** Rules whose cost runs through tonight's sleep: the recorded night carries it, the forecast names it. */
const SLEEP_MEDIATED = new Set<RuleId>(["caffeine", "exercise_timing", "screens", "phone_in_bed", "nap"]);

/** Rules whose finding keeps its own effect, through `weightOn`; last_meal for the body only. */
const BY_RULE = new Set<RuleId>([
  "morning_light",
  "nature",
  "conversation",
  "uv",
  "eating_window",
  "food_quality",
  "sedentary",
  "stress",
  "air",
  "sleep_deep",
  "wake_anchor",
  "social_jetlag",
  "sleep_regularity",
  "peptide",
  "last_meal",
]);

type Workout = Extract<MonthEvent, { kind: "workout" }>;

/** How much of a finding from `sourceIndex` is active on `dayIndex`. */
export function weightOn(finding: Finding, sourceIndex: number, dayIndex: number): number {
  const rule = RULES[finding.rule];
  const offset = dayIndex - sourceIndex - rule.lands;
  if (offset < 0) return 0;
  if (offset === 0) return 1;
  if (rule.decayDays <= 0) return 0;
  return Math.max(0, 1 - offset / rule.decayDays);
}

/** Findings for every day of the month, computed once. */
export function monthFindings(days: readonly Day[]): Finding[][] {
  return days.map((_, i) => evaluateDay(days, i));
}

/** Cognition and Body for `days[index]`, with what moved them. Pure: the same inputs give the same answer. */
export function operatingFor(days: readonly Day[], index: number, findings: Finding[][] = monthFindings(days)): Operating {
  const day = days[index];
  const prev = index > 0 ? days[index - 1] : null;
  const today = findings[index] ?? [];
  const yesterday = index > 0 ? (findings[index - 1] ?? []) : [];
  const series = seriesTo(days, index);
  const calibration = calibrationFor(series);
  const contributions: Contribution[] = [];
  let cognition = 1;
  let body = 1;
  const add = (finding: Finding, weight: number, c: number, b: number, line: string): void => {
    if (c <= 0 && b <= 0) return;
    cognition *= 1 - c;
    body *= 1 - b;
    contributions.push({ finding: { ...finding, line }, weight, cognition: c * 100, body: b * 100 });
  };

  const nightRecorded = day.sleep.minutes > 0;
  const sick = day.type === "sick";
  const postpartum = day.sleep.wakings.some((w) => w.baby);
  const drinksLastNight = prev ? drinksOn(prev) : 0;
  const drinksTwoAgo = index > 1 ? drinksOn(days[index - 2]) : 0;

  // 1. Sleep debt, for both ceilings; the line names last night and its cause.
  const debtHours = recordedDebtHours(days, index);
  const debtFinding = today.find((f) => f.rule === "sleep_debt") ?? synth("sleep_debt", day.date, day.sleep.wake);
  add(debtFinding, 1, Math.min(COST.debtCap, COST.debtPerHour * debtHours), COST.debtBodyPerHour * Math.min(DEBT_CAP_HOURS, debtHours), debtLine(days, index, debtHours));

  // 2. Tonight's forecast from today's broken sleep rules: the lever, never today's cost.
  // Only for today, or a day whose next night is not recorded; a recorded night already holds the answer.
  const forecastable = index === days.length - 1 || !((days[index + 1]?.sleep.minutes ?? 0) > 0);
  const forecast = new Map<RuleId, number>();
  if (forecastable) {
    for (const f of today) {
      const minutes = sleepMinutesOf(f);
      if (minutes > 0) forecast.set(f.rule, (forecast.get(f.rule) ?? 0) + minutes);
    }
  }
  const sleepLossForecastMin = [...forecast.values()].reduce((a, b) => a + b, 0);

  // 3. Last night's drinks: the next day in full, half the day after.
  if (prev && drinksLastNight > 0) {
    const cost = Math.min(COST.drinksCap, COST.perDrink * drinksLastNight);
    add(
      alcoholFinding(yesterday, prev),
      1,
      cost,
      0,
      `${drinks(drinksLastNight)} last night: attention and speed down (Gunn 2018). ${pct(COST.perDrink)} of cognition per drink, capped at ${pct(COST.drinksCap)}, assumed from Gunn's g of about 0.5.`,
    );
  }
  if (drinksTwoAgo > 0) {
    const cost = COST.drinksResidual * Math.min(COST.drinksCap, COST.perDrink * drinksTwoAgo);
    add(
      alcoholFinding(findings[index - 2] ?? [], days[index - 2]),
      COST.drinksResidual,
      cost,
      0,
      `${drinks(drinksTwoAgo)} two nights ago: half the next-day cost still carried, ${pct(cost)} of cognition, an assumed residual (Gunn 2018).`,
    );
  }

  // 4. Room CO2 while indoors, by the share of a 16 h waking day spent in that air.
  const co2Minutes = { amber: 0, red: 0 };
  for (const f of today) {
    if (f.rule !== "co2" || (f.tone !== "watch" && f.tone !== "violation")) continue;
    const e = eventOf(day, f.eventId);
    if (!e || e.kind !== "co2") continue;
    const red = f.tone === "violation";
    if (red) co2Minutes.red += e.minutes;
    else co2Minutes.amber += e.minutes;
    const cost = (red ? COST.co2Red : COST.co2Amber) * (e.minutes / WAKING_MINUTES);
    add(
      f,
      1,
      cost,
      0,
      `${e.label}, ${e.ppm.toLocaleString("en-US")} ppm for ${hm(e.minutes)}: ${e.minutes} of ${WAKING_MINUTES} waking minutes in air that costs ${pct(red ? COST.co2Red : COST.co2Amber)} of decisions, ${pct(cost)} of the day, assumed from Allen's -15% at 945 ppm and -50% at 1,400 ppm${red ? ", the red band counted at 30% as a floor of the 15 to 50% range" : ""}.`,
    );
  }

  // 5. Water by 18:00, scaled by the shortfall.
  const water = waterMl(day);
  const hydration = today.find((f) => f.rule === "hydration");
  if (hydration && day.until >= WINDOWS.waterBy) {
    const shortfall = Math.max(0, 1 - water / WINDOWS.waterTarget);
    // A shortfall that rounds to 0% costs nothing worth a line.
    if (Math.round(shortfall * 100) > 0) {
      add(
        hydration,
        1,
        COST.hydration * shortfall,
        COST.hydrationBody * shortfall,
        `${(water / 1000).toFixed(1)} of 2 L by ${clock(WINDOWS.waterBy)}: ${pct(COST.hydration)} of cognition and ${pct(COST.hydrationBody)} of body at no water, scaled by the ${Math.round(shortfall * 100)}% shortfall, assumed from Armstrong and Ganio's 1.4 to 1.6% body-water loss.`,
      );
    }
  }

  // 6. A nap over 30 min: grogginess for 35 to 95 min, averaged over the waking day.
  let napInertiaMin = 0;
  for (const e of day.events) if (e.kind === "nap" && e.minutes > 30) napInertiaMin = Math.max(napInertiaMin, e.minutes);
  for (const f of today) {
    if (f.rule !== "nap") continue;
    const e = eventOf(day, f.eventId);
    if (!e || e.kind !== "nap" || e.minutes <= 30) continue;
    add(f, 1, COST.napInertia, 0, `${e.minutes} min nap at ${clock(e.start)}: about 41% worse on waking for 35 to 95 min (Brooks & Lack), 65 of ${WAKING_MINUTES} waking minutes, ${pct(COST.napInertia)} of the day, assumed.`);
  }

  // 7. Not the wearer's decision: a night broken by the baby, a sick day.
  const broken = today.find((f) => f.rule === "postpartum");
  if (broken) {
    add(broken, 1, COST.postpartum, COST.postpartumBody, `${broken.line} ${pct(COST.postpartum)} of cognition and ${pct(COST.postpartumBody)} of body today, ${pct(COST.postpartumNext)} of cognition tomorrow, assumed from Insana's slowest reaction times.`);
  }
  const brokenBefore = yesterday.find((f) => f.rule === "postpartum");
  if (brokenBefore) {
    add(brokenBefore, 0.5, COST.postpartumNext, 0, `${brokenBefore.line} Half of that night's ${pct(COST.postpartum)} still carried, ${pct(COST.postpartumNext)} of cognition, assumed from Insana's slowest reaction times.`);
  }
  const sickFinding = today.find((f) => f.rule === "sick");
  if (sickFinding) {
    add(sickFinding, 1, COST.sick, COST.sickBody, `${RULES.sick.consequence} ${pct(COST.sick)} of cognition and ${pct(COST.sickBody)} of body, a protocol default, assumed.`);
  }

  // 8. Everything else keeps its rule effect, landing and fading as the rule says.
  // Sleep-mediated rules from yesterday count only when last night went unrecorded,
  // and then on the same debt curve as a recorded night: their minutes at 0.5% an hour.
  let airCounted = false;
  for (let j = Math.max(0, index - LOOKBACK); j <= index; j++) {
    for (const f of findings[j] ?? []) {
      const weight = weightOn(f, j, index);
      if (weight <= 0) continue;
      if (!nightRecorded && SLEEP_MEDIATED.has(f.rule)) {
        const minutes = sleepMinutesOf(f) * weight;
        if (minutes <= 0) continue;
        const cost = (COST.debtPerHour * minutes) / 60;
        add(f, weight, cost, 0, `${f.line} Last night was not recorded, so its ${hm(Math.round(minutes))} of forecast loss is charged at ${pct(COST.debtPerHour)} of cognition an hour, ${pct(cost)}, assumed.`);
        continue;
      }
      if (!BY_RULE.has(f.rule)) continue;
      if (!f.cognition && !f.body) continue;
      // Bad air is a day-level term: one outing carries it, the rest of the day's outings do not repeat it.
      if (f.rule === "air") {
        if (airCounted) continue;
        airCounted = true;
      }
      add(f, weight, (f.rule === "last_meal" ? 0 : f.cognition) * weight, f.body * weight, f.line);
    }
  }

  // Body 1. HRV and resting HR against the wearer's baseline.
  const hrv = day.sleep.hrv_ms;
  const rhr = day.sleep.rhr_bpm;
  const baselineHrv = calibration.ready && calibration.hrvBestMs !== null ? calibration.hrvBestMs : medianOf(series.hrv);
  const baselineRhr = lowestWindowMedian(series.rhr) ?? medianOf(series.rhr);
  const estimated = drinksLastNight > 0 && (hrv === null || rhr === null);
  const hrvUsed = hrv ?? (drinksLastNight > 0 && baselineHrv !== null ? baselineHrv - HRV_PER_DRINK * drinksLastNight : null);
  const rhrUsed = rhr ?? (drinksLastNight > 0 && baselineRhr !== null ? baselineRhr + RHR_PER_DRINK * drinksLastNight : null);
  const hrvDelta = hrvUsed !== null && baselineHrv !== null ? hrvUsed - baselineHrv : null;
  const rhrDelta = rhrUsed !== null && baselineRhr !== null ? rhrUsed - baselineRhr : null;
  const costHrv = hrvDelta !== null && baselineHrv ? Math.min(COST.hrvCap, (COST.hrvShare * Math.max(0, -hrvDelta)) / baselineHrv) : 0;
  const costRhr = rhrDelta !== null ? Math.min(COST.rhrCap, COST.rhrPerBpm * Math.max(0, rhrDelta)) : 0;
  if (costHrv > 0 || costRhr > 0) {
    const shifts = [hrvDelta !== null ? `HRV ${signed(hrvDelta)} ms` : null, rhrDelta !== null ? `resting HR ${signed(rhrDelta)} bpm` : null].filter(Boolean).join(", ");
    const baseline = `against your baseline${baselineHrv !== null && baselineRhr !== null ? ` of ${fmt(baselineHrv)} ms and ${fmt(baselineRhr)} bpm` : ""}`;
    const guess = estimated ? ` Last night's watch reading is missing, so the shift is estimated from Grosicki's ${HRV_PER_DRINK} ms and ${RHR_PER_DRINK} bpm per drink, assumed midpoints.` : "";
    const cost = 1 - (1 - costHrv) * (1 - costRhr);
    if (prev && drinksLastNight > 0) {
      add(alcoholFinding(yesterday, prev), 1, 0, cost, `${drinks(drinksLastNight)}: ${shifts} ${baseline} (Grosicki 2026).${guess} ${BASELINE_ASSUMED}`);
    } else if (broken) {
      add(broken, 1, 0, cost, `Broken night: ${shifts} ${baseline}. Not your decision. ${BASELINE_ASSUMED}`);
    } else if (sickFinding) {
      add(sickFinding, 1, 0, cost, `Sick: ${shifts} ${baseline}. ${BASELINE_ASSUMED}`);
    } else {
      add(debtFinding, 1, 0, cost, `${shifts.charAt(0).toUpperCase()}${shifts.slice(1)} ${baseline}. ${BASELINE_ASSUMED}`);
    }
  }

  // Body 3. Recovery from a hard workout yesterday.
  let hardExerciseYesterday = false;
  if (prev) {
    const hard = prev.events
      .filter((e): e is Workout => e.kind === "workout" && e.vigorous && e.minutes >= COST.hardExerciseMinutes)
      .sort((a, b) => b.minutes - a.minutes)[0];
    if (hard) {
      hardExerciseYesterday = true;
      const f = yesterday.find((x) => x.rule === "exercise_timing" && x.eventId === hard.id) ?? synth("exercise_timing", prev.date, hard.start, hard.id);
      // A seeded label may already carry its minutes ("Strength, 45 min"); name them once.
      const label = lower(hard.label.replace(/,\s*\d+\s*min$/i, ""));
      add(f, 1, 0, COST.hardExercise, `Recovering from yesterday's ${hard.minutes} min ${label}: ${pct(COST.hardExercise)} of body, assumed.`);
    }
  }

  contributions.sort((a, b) => b.cognition + b.body - (a.cognition + a.body));
  const date = day.date;
  const pvtToday = morningPvt(day);
  return {
    date,
    cognition: round1(cognition * 100),
    body: round1(body * 100),
    contributions,
    top: topReasons(contributions, date),
    lever: leverFor(findings, index, forecast),
    calibration,
    measured: {
      cognition: calibration.ready && calibration.pvtBestMs !== null && pvtToday ? Math.min(100, round1((calibration.pvtBestMs / pvtToday) * 100)) : null,
      body: calibration.ready && calibration.hrvBestMs ? (hrv !== null ? Math.min(100, round1((hrv / calibration.hrvBestMs) * 100)) : null) : null,
    },
    inputs: {
      sleepDebtHours: debtHours,
      drinksLastNight,
      sleepLossForecastMin,
      co2Minutes,
      waterMl: water,
      napInertiaMin,
      postpartum,
      sick,
      hrvMs: hrv,
      rhrBpm: rhr,
      hardExerciseYesterday,
    },
  };
}

/** Every day of the month. */
export function operatingMonth(days: readonly Day[]): Operating[] {
  const findings = monthFindings(days);
  return days.map((_, i) => operatingFor(days, i, findings));
}

// ---------------------------------------------------------------------------
// Calibration: the best week of PVT and HRV so far
// ---------------------------------------------------------------------------

interface Series {
  pvt: (number | null)[];
  hrv: (number | null)[];
  rhr: (number | null)[];
}

/** Morning PVT, HRV and resting HR for every day up to and including `index`. */
function seriesTo(days: readonly Day[], index: number): Series {
  const pvt: (number | null)[] = [];
  const hrv: (number | null)[] = [];
  const rhr: (number | null)[] = [];
  for (let i = 0; i <= index; i++) {
    pvt.push(morningPvt(days[i]));
    hrv.push(days[i].sleep.hrv_ms);
    rhr.push(days[i].sleep.rhr_bpm);
  }
  return { pvt, hrv, rhr };
}

/** The first PVT-B before noon, ms; null when none was taken. */
function morningPvt(day: Day): number | null {
  for (const e of day.events) if (e.kind === "mind_check" && e.start < NOON) return e.ms;
  return null;
}

function calibrationFor(series: Series): Calibration {
  let count = 0;
  for (let i = 0; i < series.pvt.length; i++) if (series.pvt[i] !== null && series.hrv[i] !== null) count += 1;
  let pvtBest: number | null = null;
  let hrvBest: number | null = null;
  if (count >= CALIBRATION_DAYS) {
    for (let j = CALIBRATION_DAYS - 1; j < series.pvt.length; j++) {
      const p = windowMedian(series.pvt, j);
      if (p !== null && (pvtBest === null || p < pvtBest)) pvtBest = p;
      const h = windowMedian(series.hrv, j);
      if (h !== null && (hrvBest === null || h > hrvBest)) hrvBest = h;
    }
    // Seven days of readings with no window dense enough for a rolling median
    // (a PVT every third morning, say): the median of every reading so far
    // stands in, so ready flips with the day count as the label promises.
    pvtBest ??= medianOf(series.pvt);
    hrvBest ??= medianOf(series.hrv);
  }
  const ready = pvtBest !== null && hrvBest !== null;
  const days = Math.min(CALIBRATION_DAYS, count);
  return {
    days,
    ready,
    pvtBestMs: ready ? pvtBest : null,
    hrvBestMs: ready ? hrvBest : null,
    label: ready ? `your 100%: PVT ${Math.round(pvtBest as number)} ms, HRV ${Math.round(hrvBest as number)} ms` : `calibrating, ${days} of ${CALIBRATION_DAYS} days`,
  };
}

/** The median of the readings in the 7-day window ending at `end`; null unless the window spans 7 days and holds at least 4 readings. */
function windowMedian(values: readonly (number | null)[], end: number): number | null {
  if (end < CALIBRATION_DAYS - 1) return null;
  const xs: number[] = [];
  for (let i = end - CALIBRATION_DAYS + 1; i <= end; i++) {
    const v = values[i];
    if (v !== null && v !== undefined) xs.push(v);
  }
  return xs.length >= MIN_WINDOW_READINGS ? median(xs) : null;
}

/** The lowest 7-day rolling median in the series; null before any window qualifies. */
function lowestWindowMedian(values: readonly (number | null)[]): number | null {
  let best: number | null = null;
  for (let j = CALIBRATION_DAYS - 1; j < values.length; j++) {
    const m = windowMedian(values, j);
    if (m !== null && (best === null || m < best)) best = m;
  }
  return best;
}

/** The median of every recorded reading so far; null when none. */
function medianOf(values: readonly (number | null)[]): number | null {
  const xs = values.filter((v): v is number => v !== null);
  return xs.length ? median(xs) : null;
}

function median(xs: readonly number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

// ---------------------------------------------------------------------------
// Lines, labels and helpers
// ---------------------------------------------------------------------------

/** Hours under 8 h summed over the last 14 recorded nights; an unrecorded night (0 min) is not a night of debt. */
function recordedDebtHours(days: readonly Day[], index: number): number {
  let debt = 0;
  for (let i = Math.max(0, index - DEBT_NIGHTS + 1); i <= index; i++) {
    const minutes = days[i].sleep.minutes;
    if (minutes > 0) debt += Math.max(0, FULL_NIGHT - minutes);
  }
  return debt / 60;
}

/** N: consecutive recorded nights under 7 h ending this morning; an unrecorded night ends the run. */
function shortNightRun(days: readonly Day[], index: number): number {
  let n = 0;
  for (let i = index; i >= 0; i--) {
    const minutes = days[i].sleep.minutes;
    if (minutes <= 0 || minutes >= SHORT_NIGHT) break;
    n += 1;
  }
  return n;
}

/** Minutes of tonight's sleep a broken sleep-mediated rule is forecast to cost, scaled like the finding's own effect. */
function sleepMinutesOf(f: Finding): number {
  if (!SLEEP_MEDIATED.has(f.rule) || (f.tone !== "violation" && f.tone !== "watch")) return 0;
  if (f.rule === "nap" && f.tone !== "violation") return 0; // a long nap costs the waking day, not the night
  const rule = RULES[f.rule];
  const scale = rule.effect.cognition > 0 ? f.cognition / rule.effect.cognition : 1;
  return rule.sleepMinutes * scale;
}

/** "Sleep debt 6.2 h; last night 48 min short after yesterday's 16:10 coffee. You feel fine. Your reaction time says day 3 of short sleep. 0.5% ..." */
function debtLine(days: readonly Day[], index: number, debtHours: number): string {
  const day = days[index];
  const short = Math.max(0, FULL_NIGHT - day.sleep.minutes);
  const cause = index > 0 ? sleepCause(days[index - 1]) : null;
  const n = shortNightRun(days, index);
  const night = day.sleep.minutes <= 0 ? "not recorded" : short > 0 ? `${hm(short)} short${cause ? ` after ${cause}` : ""}` : `${hm(day.sleep.minutes)}, nothing short`;
  let line = `Sleep debt ${fmt(debtHours)} h; last night ${night}.`;
  if (n >= 1) line += ` ${RULES.sleep_debt.consequence.replace(/\bN\b/, String(n))}`;
  return `${line} ${DEBT_ASSUMED}`;
}

/** Yesterday's first alcohol finding, or a day-level one when the day had none drawn (a sick day). */
function alcoholFinding(findings: readonly Finding[], day: Day): Finding {
  const drawn = findings.find((f) => f.rule === "alcohol");
  if (drawn) return drawn;
  const first = day.events.find((e) => e.kind === "alcohol");
  return synth("alcohol", day.date, first?.start ?? WINDOWS.sleepStart);
}

/** A day-level finding with no effect of its own, for a term whose rule drew nothing that day. */
function synth(rule: RuleId, date: string, time: number, eventId?: string): Finding {
  return { rule, date, eventId, time, tone: "neutral", line: "", cognition: 0, body: 0 };
}

function eventOf(day: Day, id: string | undefined): MonthEvent | undefined {
  return id ? day.events.find((e) => e.id === id) : undefined;
}

function drinksOn(day: Day): number {
  return day.events.reduce((n, e) => (e.kind === "alcohol" ? n + e.drinks : n), 0);
}

function drinks(n: number): string {
  return `${n} ${n === 1 ? "drink" : "drinks"}`;
}

/** Merge a rule's contributions (three drinks are one reason), then name the two biggest. */
function topReasons(contributions: Contribution[], date: string): string[] {
  const byRule = new Map<string, { label: string; total: number }>();
  for (const c of contributions) {
    const key = `${c.finding.rule}:${c.finding.date}`;
    const existing = byRule.get(key);
    const total = c.cognition + c.body;
    if (existing) existing.total += total;
    else byRule.set(key, { label: reasonLabel(c.finding, date), total });
  }
  return [...byRule.values()]
    .sort((a, b) => b.total - a.total)
    .slice(0, 2)
    .map((r) => r.label);
}

/** "Coffee at 16:10 yesterday", "Broken sleep last night", "Hard workout at 15:30 yesterday". */
export function reasonLabel(f: Finding, onDate: string): string {
  const when = f.date === onDate ? "" : ` ${relativeDay(f.date, onDate)}`;
  const at = f.eventId ? ` at ${clock(f.time)}` : "";
  // A workout inside its window only shows up as recovery the day after.
  const name = f.rule === "exercise_timing" && f.tone !== "violation" && f.tone !== "watch" ? "Hard workout" : SHORT[f.rule];
  if (f.rule === "postpartum" || f.rule === "sleep_debt" || f.rule === "sleep_deep") {
    return f.date === onDate ? `${name} last night` : `${name}${when}`;
  }
  return `${name}${at}${when}`;
}

const SHORT: Record<RuleId, string> = {
  caffeine: "Coffee",
  sleep_debt: "Sleep debt",
  alcohol: "Drinks",
  exercise_timing: "Late workout",
  last_meal: "Late dinner",
  screens: "Screens after 21:30",
  phone_in_bed: "Phone in bed",
  co2: "Stale air",
  sleep_regularity: "Bedtime drift",
  social_jetlag: "Social jetlag",
  morning_light: "No morning light",
  nature: "Little daylight",
  conversation: "Little time with people",
  hydration: "Under 2 L of water",
  nap: "Late nap",
  sauna: "Sauna",
  postpartum: "Broken sleep",
  skipped_meal: "Skipped meal",
  sick: "Sick day",
  peptide: "Missed dose",
  uv: "Midday sun",
  eating_window: "Early first meal",
  food_quality: "Processed food",
  sedentary: "Long seated block",
  stress: "Stress spike",
  air: "Bad air",
  sleep_deep: "Little deep sleep",
  wake_anchor: "Wake time off",
};

/**
 * The biggest lever for tomorrow: today's forecast when a sleep rule was broken
 * (the rule with the most minutes), else the rule that cost most over the last
 * seven days, else null.
 */
function leverFor(findings: Finding[][], index: number, forecast: Map<RuleId, number>): Operating["lever"] {
  const pick = (m: Map<RuleId, number>) => [...m.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
  const tonight = pick(forecast);
  if (tonight) {
    // The lever names what fixing this one rule recovers; the rest are named, not folded in.
    const minutes = forecast.get(tonight) ?? 0;
    const cost = (COST.debtPerHour * minutes) / 60;
    const total = [...forecast.values()].reduce((a, b) => a + b, 0);
    const others = [...forecast.keys()].filter((r) => r !== tonight).map((r) => lower(SHORT[r]));
    const rest = others.length ? ` With ${others.join(" and ")}, about ${hm(Math.round(total))} in all.` : "";
    return { rule: tonight, text: `${RULES[tonight].lever}. Tonight about ${hm(Math.round(minutes))} short, tomorrow's cognition about ${pct(cost)} lower, assumed.${rest}` };
  }
  const week = new Map<RuleId, number>();
  for (let j = Math.max(0, index - 6); j <= index; j++) {
    for (const f of findings[j] ?? []) {
      if (f.tone !== "violation" && f.tone !== "watch") continue;
      week.set(f.rule, (week.get(f.rule) ?? 0) + f.cognition + f.body);
    }
  }
  const rule = pick(week);
  return rule ? { rule, text: RULES[rule].lever } : null;
}

function relativeDay(date: string, onDate: string): string {
  const diff = Math.round((Date.parse(`${onDate}T12:00:00Z`) - Date.parse(`${date}T12:00:00Z`)) / 86_400_000);
  if (diff === 1) return "yesterday";
  if (diff === 2) return "two days ago";
  return `${diff} days ago`;
}

function round1(x: number): number {
  return Math.round(x * 10) / 10;
}

/** "28", "6.2", "0.4": one decimal at most. */
function fmt(x: number): string {
  return String(round1(x));
}

/** "-10.5", "+7.8". */
function signed(x: number): string {
  return x < 0 ? `-${fmt(-x)}` : `+${fmt(x)}`;
}

/** A fraction as "6%", "0.4%". */
function pct(fraction: number): string {
  return `${fmt(fraction * 100)}%`;
}

function lower(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}
