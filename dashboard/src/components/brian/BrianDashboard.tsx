"use client";
import { T } from "@/lib/tokens";
import type { DashboardData, Goal } from "@/lib/score/types";
import { BrianHeader } from "./Header";
import { Effects } from "./Effects";
import { Evidence } from "./Evidence";
import { Layers } from "./Layers";
import { Levers } from "./Levers";
import { SevenDays } from "./SevenDays";
import { Today } from "./Today";
import { Tonight } from "./Tonight";
import { WeekLedger } from "./WeekLedger";

export interface BrianDashboardProps {
  data: DashboardData;
  goal: Goal;
  /** Absent on pages that cannot re-score live (the select then navigates with `?goal=`). */
  onGoalChange?: (goal: Goal) => void;
  /** True while a fresh payload is being fetched behind the one on screen. */
  updating?: boolean;
}

/*
  Project Brian — dashboard. Minimalism / Swiss in the visual language of the
  WHOOP reference: black wordmark bar, white page, soft grey containers with a
  20px radius, one bold sans (DM Sans), and colour reserved for data: green
  earns, red costs. No gauges, no rings, no neon, no monospace. The one bold
  element is the evidence strip — a real frame from the glasses pinned to what
  it earned or cost tonight. Everything drawn here comes from `data`.
*/
export function BrianDashboard({ data, goal, onGoalChange, updating = false }: BrianDashboardProps) {
  return (
    <div className="brian min-h-dvh bg-bg text-text">
      <BrianHeader person={data.person} source={data.source} goal={goal} onGoalChange={onGoalChange} active="today" />
      <main className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-8 sm:px-6">
        <div className="grid grid-cols-1 gap-6 md:grid-cols-12">
          <Today d={data} updating={updating} />
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
