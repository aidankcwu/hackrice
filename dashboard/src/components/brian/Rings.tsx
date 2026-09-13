import { T } from "@/lib/tokens";
import type { ActivityRing, ActivityRings, DashboardData } from "@/lib/score/types";
import { RING_GOAL_EXERCISE_MIN, RING_GOAL_MOVE_KCAL, RING_GOAL_STEPS } from "@/lib/score/units";
import { H2, Panel } from "./Panel";

/*
  Activity rings — three concentric arcs in the Apple Fitness idiom, reading the
  wrist device and the glasses rather than inventing a day:

    Move      active_energy kcal today (the Fitbit's calorie burn)
    Steps     steps today
    Exercise  gym_session + outdoor_block minutes from the glasses episodes

  Honesty is the whole point of the component. A ring whose metric has no real
  source today draws an empty track, prints an em dash, and says "not measured"
  — never a zero arc that looks like a measured zero, never a goal credited to
  a number nobody took. That mirrors the engine, which imputes an unmeasured
  factor at the population reference and credits it no hours.

  Colour stays inside the palette: the ring is ink on the surface-2 track, and
  green appears only once the goal is actually met (green earns, as everywhere
  else on the page). No neon, no gradients. Over-achievement keeps sweeping past
  100 % — the second lap draws on top in earn green with a brighter round cap,
  so 140 % is visibly more than 100 % without a second legend.
*/

/** Geometry: one viewBox, three radii, stroke 14 with rounded caps. */
const BOX = 240;
const C = BOX / 2;
const STROKE = 14;
const GAP = 6;
const RADII = [C - STROKE / 2 - 2, C - STROKE / 2 - 2 - (STROKE + GAP), C - STROKE / 2 - 2 - 2 * (STROKE + GAP)] as const;

/** Fraction of the goal, clamped at 2 laps so a pathological number cannot spin forever. */
const fraction = (ring: ActivityRing): number =>
  ring.value === null || ring.goal <= 0 ? 0 : Math.max(0, Math.min(2, ring.value / ring.goal));

const fmtValue = (ring: ActivityRing): string =>
  ring.value === null ? "—" : Math.round(ring.value).toLocaleString("en-US");

/**
 * The provenance line under each number. A measured ring names its stream
 * (`fitbit`, `glasses`, `seeded`); an unmeasured one keeps naming the stream
 * that *would* have filed it and says plainly that it did not.
 */
const fmtSource = (ring: ActivityRing): string =>
  ring.value === null ? `not measured · ${ring.source}` : ring.source;

/** "412 of 500 kcal" / "not measured" — one clause of the group's aria-label. */
const fmtAria = (ring: ActivityRing): string =>
  ring.value === null
    ? `${ring.label}: not measured today, so it counts for nothing`
    : `${ring.label}: ${Math.round(ring.value).toLocaleString("en-US")} of ${ring.goal.toLocaleString("en-US")} ${ring.unit}, ${Math.round((ring.value / ring.goal) * 100)} percent of the goal`;

/**
 * The sweep when a ring's value changes. It is a plain CSS transition and it is
 * an inline style without `!important`, so the `.brian *` reduced-motion rule in
 * globals.css (`transition: none !important`) disables it outright — there is no
 * JS animation to gate, and nothing moves for a wearer who asked for stillness.
 */
const SWEEP = { transition: "stroke-dashoffset 250ms ease, stroke 250ms ease" } as const;

interface ArcProps {
  radius: number;
  /** 0–2 laps of the goal. */
  value: number;
}

/**
 * One ring. The first lap is ink, the overflow lap is drawn over it in earn
 * green so passing the goal reads as a brighter cap rather than a reset arc.
 * Rotated −90° so both laps start at twelve o'clock, like a watch face.
 */
function Arc({ radius, value }: ArcProps) {
  const circumference = 2 * Math.PI * radius;
  const lap = Math.min(1, value);
  const over = Math.max(0, value - 1);
  return (
    <g transform={`rotate(-90 ${C} ${C})`}>
      <circle cx={C} cy={C} r={radius} fill="none" stroke={T.surface2} strokeWidth={STROKE} />
      {lap > 0 && (
        <circle
          cx={C}
          cy={C}
          r={radius}
          fill="none"
          stroke={value >= 1 ? T.earn : T.ink}
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - lap)}
          style={SWEEP}
        />
      )}
      {over > 0 && (
        <circle
          cx={C}
          cy={C}
          r={radius}
          fill="none"
          stroke={T.earn}
          strokeWidth={STROKE - 5}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - Math.min(1, over))}
          style={SWEEP}
        />
      )}
    </g>
  );
}

/** The number / goal / source block beside the rings, one row per ring. */
function Legend({ ring }: { ring: ActivityRing }) {
  const met = ring.value !== null && ring.value >= ring.goal;
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden="true"
        className="mt-1 block shrink-0 rounded-full"
        style={{
          width: 12,
          height: 12,
          border: `3px solid ${ring.value === null ? T.surface2 : met ? T.earn : T.ink}`,
        }}
      />
      <div className="min-w-0">
        <div className="text-sm font-medium" style={{ color: T.muted }}>
          {ring.label}
        </div>
        <div className="tnum text-2xl font-bold leading-tight" style={{ color: ring.value === null ? T.muted : T.ink }}>
          {fmtValue(ring)}
          <span className="text-base font-medium" style={{ color: T.muted }}>
            {ring.value === null ? "" : ` / ${ring.goal.toLocaleString("en-US")} ${ring.unit}`}
          </span>
        </div>
        <div className="truncate text-sm" style={{ color: T.muted }}>
          {fmtSource(ring)}
        </div>
      </div>
    </li>
  );
}

export interface RingsProps {
  /** Ready-made rings from the loader lane, when it supplies them. */
  activity?: ActivityRings;
  /** The payload the rings fall back to when `activity` is absent. */
  d: DashboardData;
}

export function Rings({ activity, d }: RingsProps) {
  const rings = activity ?? ringsFromPayload(d);
  const order = [rings.move, rings.steps, rings.exercise];
  const label = `Activity rings. ${order.map(fmtAria).join(". ")}.`;

  return (
    <Panel id="activity" labelledBy="activity-title" className="md:col-span-7">
      <H2 id="activity-title" sub="Today against conventional daily targets — not medical thresholds.">
        Activity
      </H2>
      <div className="flex flex-col items-center gap-6 sm:flex-row sm:items-center sm:gap-8">
        <svg
          role="img"
          aria-label={label}
          viewBox={`0 0 ${BOX} ${BOX}`}
          className="h-auto w-full max-w-[200px] shrink-0 sm:max-w-[220px]"
        >
          {order.map((ring, i) => (
            <Arc key={ring.key} radius={RADII[i]} value={fraction(ring)} />
          ))}
        </svg>
        {/* The same three numbers as text: the SVG is one image to a screen
            reader, so the list is what a keyboard user actually walks. */}
        <ul className="m-0 flex min-w-0 flex-1 list-none flex-col gap-4 p-0">
          {order.map((ring) => (
            <Legend key={ring.key} ring={ring} />
          ))}
        </ul>
      </div>
    </Panel>
  );
}

/**
 * What today's engine payload already proves, for the case where the loader
 * lane has not attached `activity` yet.
 *
 * `observations.steps` is the wearable's own step count for the day, and the
 * matching factor carries whether it was measured and which stream it came
 * from, so Steps is honest here. `day_light_min` is the glasses' outdoor-block
 * minutes and `resistance_min_wk` the week's gym minutes, which is the wrong
 * window for a day's exercise ring — so Exercise only fills from a loader that
 * sums today's two episode kinds. Active energy is not an engine observation at
 * all: there is no kcal in this payload, and inventing one is exactly what R1
 * forbids, so Move stays not measured until the wearable lane feeds it.
 */
export function ringsFromPayload(d: DashboardData): ActivityRings {
  const stepsFactor = d.factors.find((f) => f.key === "steps");
  const stepsValue = stepsFactor?.measured === true ? (d.observations.steps ?? stepsFactor.dose) : null;
  return {
    move: { key: "move", label: "Move", value: null, goal: RING_GOAL_MOVE_KCAL, unit: "kcal", source: "fitbit" },
    steps: {
      key: "steps",
      label: "Steps",
      value: typeof stepsValue === "number" && Number.isFinite(stepsValue) ? stepsValue : null,
      goal: RING_GOAL_STEPS,
      unit: "steps",
      source: d.source.mode === "live" ? "fitbit" : "seeded",
    },
    exercise: {
      key: "exercise",
      label: "Exercise",
      value: null,
      goal: RING_GOAL_EXERCISE_MIN,
      unit: "min",
      source: "glasses",
    },
  };
}

export default Rings;
