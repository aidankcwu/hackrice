"use client";
import { T } from "@/lib/tokens";
import { minutesOfInstant } from "@/lib/score/narrative";
import type { DashboardData, Goal } from "@/lib/score/types";
import { BrianHeader } from "./Header";
import { BryanSaid } from "./BryanSaid";
import { Effects } from "./Effects";
import { Evidence } from "./Evidence";
import { Instruments } from "./Instruments";
import { Layers } from "./Layers";
import { NextBest } from "./NextBest";
import { SevenDays } from "./SevenDays";
import { WearableNumbers } from "./WearableNumbers";
import { Today } from "./Today";
import { Tonight } from "./Tonight";
import { WeekLedger } from "./WeekLedger";
import { Logs } from "./Logs";
import { PersonaPanel } from "@/components/PersonaPanel";

export interface BrianDashboardProps {
  data: DashboardData;
  goal: Goal;
  /** Absent on pages that cannot re-score live (the select then navigates with `?goal=`). */
  onGoalChange?: (goal: Goal) => void;
  /** True while a fresh payload is being fetched behind the one on screen. */
  updating?: boolean;
}

/*
  Bryan — dashboard. Minimalism / Swiss in the visual language of the WHOOP
  reference: black wordmark bar, white page, soft grey containers with a 20px
  radius, one bold sans (DM Sans), and colour reserved for data: green earns,
  red costs. No neon, no gradients, no monospace, and no gauges or rings
  (SKILL.md law 5): the wrist device's own numbers live at the bottom, under
  "The numbers your wearable already knows". The one bold element is the
  evidence strip — a real frame from the glasses pinned to what it earned or
  cost tonight. Everything drawn here comes from `data`.
*/
export function BrianDashboard({ data, goal, onGoalChange, updating = false }: BrianDashboardProps) {
  return (
    <div className="brian min-h-dvh bg-bg text-text">
      <BrianHeader person={data.person} source={data.source} />
      <main className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-8 sm:px-6">
        {/* The persona T1 is briefed with, first: it is the one thing the
            operator edits mid-demo (include or exclude what the glasses care
            about), so it is not buried in the pipeline drawer. Light skin so it
            sits flush with Today and Activity. */}
        <PersonaPanel variant="light" />
        {/* Logs: recorded sessions, newest first. Above Today because a judge
            session is the thing being demonstrated, and its report is the
            payload -- Today is the day it happens to sit inside. */}
        <Logs />
        {/* §1.1 beside §1.2: the ledger, then the five layers only the glasses
            measure. `provenance` is derived from the factors the payload
            carries: a factor the engine did not measure is `missing`, so its
            tile says so rather than showing a number nothing produced.
            `trailing` stays empty until the days=7 window is wired — the tiles
            read that as "no sparkline", never a flat line at zero. */}
        <div className="grid grid-cols-1 gap-6 md:grid-cols-12">
          <Today d={data} updating={updating} />
          <Instruments
            source={{
              observations: data.observations,
              provenance: Object.fromEntries(
                data.factors.map((f) => [
                  f.key,
                  f.measured
                    ? { source: "seeded" as const, basis: "whoop", detail: f.label }
                    : { source: "missing" as const, basis: "glasses", detail: "Unmeasured today — scored at the population average, earns nothing." },
                ]),
              ),
              forecast: data.forecast,
              bedtime_hh: data.person.bedtime_hh,
              trailing: [],
            }}
          />
        </div>
        {/* Full width: the panel no longer carries a column span (design-system
            pass), so a 7-column wrapper left it one seventh wide. */}
        <Layers d={data} />
        {/* §1.3 then §1.4: what Bryan said, then the frames it said it about.
            `generated_at` is the minute the payload was scored at, so an
            outcome window that has not elapsed yet stays Pending. */}
        <BryanSaid pins={data.pins} nowMinute={minutesOfInstant(data.generated_at)} />
        <Evidence pins={data.pins} />
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          <Tonight
            f={data.forecast}
            observations={data.observations}
            profile={{ age: data.person.age, sex: data.person.sex, goal: data.person.goal, bedtime_hh: data.person.bedtime_hh }}
          />
          {/* §1.6. `pAdherence` is omitted, not defaulted: with no adherence
              log the row simply carries no "you do this N % of the time" line. */}
          <NextBest rows={data.levers.map((l) => ({ key: l.key, action: l.action, time: l.time, gain: l.gain }))} />
        </div>
        {/* §1.8: the wrist device's own numbers, collapsed, below the layers
            only the glasses can see. */}
        <WearableNumbers d={data} />
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          <WeekLedger ledger={data.ledger} />
          <Effects effects={data.effects} />
        </div>
        <SevenDays week={data.week} summary={data.week_summary} />
        <p className="m-0 pb-6 text-sm" style={{ color: T.muted }}>
          Hours are a day&apos;s share of the life-expectancy change implied by published hazard ratios (Gompertz
          shift, microlife framing). Observational effects are shrunk by evidence grade. Measurement and planning,
          not diagnosis.
        </p>
      </main>
    </div>
  );
}

export default BrianDashboard;
