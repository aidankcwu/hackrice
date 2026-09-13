"use client";
import { T } from "@/lib/tokens";
import type { DashboardData, Goal } from "@/lib/score/types";
import { BrianHeader } from "./Header";
import { Effects } from "./Effects";
import { Evidence } from "./Evidence";
import { Layers } from "./Layers";
import { Levers } from "./Levers";
import { Rings } from "./Rings";
import { SevenDays } from "./SevenDays";
import { Today } from "./Today";
import { Tonight } from "./Tonight";
import { WeekLedger } from "./WeekLedger";
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
  red costs. No neon, no gradients, no monospace. Two bold elements: the
  activity rings in the top row, which are the wrist device's own day, and the
  evidence strip — a real frame from the glasses pinned to what it earned or
  cost tonight. Everything drawn here comes from `data`.
*/
export function BrianDashboard({ data, goal, onGoalChange, updating = false }: BrianDashboardProps) {
  return (
    <div className="brian min-h-dvh bg-bg text-text">
      <BrianHeader person={data.person} source={data.source} goal={goal} onGoalChange={onGoalChange} active="today" />
      <main className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-8 sm:px-6">
        {/* The persona T1 is briefed with, first: it is the one thing the
            operator edits mid-demo (include or exclude what the glasses care
            about), so it is not buried in the pipeline drawer. Dark chrome
            because the panel is shared with the drawer. */}
        <div className="lifeos-dark rounded-panel p-3">
          <PersonaPanel />
        </div>
        {/* Top row: the healthspan number beside the day's activity rings.
            One column on phones, so neither can overflow. */}
        <div className="grid grid-cols-1 gap-6 md:grid-cols-12">
          <Today d={data} updating={updating} />
          <Rings activity={data.activity} d={data} />
        </div>
        {/* Layers carries its own `md:col-span-7`, so it gets a 7-column grid. */}
        <div className="grid grid-cols-1 gap-6 md:grid-cols-7">
          <Layers layers={data.layers} />
        </div>
        <Evidence pins={data.pins} />
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          <Tonight f={data.forecast} />
          <Levers levers={data.levers} />
        </div>
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
