"use client";
import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { T } from "@/lib/tokens";
import type { DashboardData, WearableStat } from "@/lib/score/types";
import { provenanceOf, contextFor } from "@/lib/score/provenance";
import { fmtInt } from "./format";
import { ProvenanceChip } from "./Panel";

/*
  The numbers your wearable already knows — screens.md §1.8.

  Last real panel on the page and collapsed by default, which is the whole
  argument: steps, strain, sleep stages and resting heart rate are table stakes,
  and they never lead (law 1). This is also where steps and calories live now
  that the activity rings are gone — a ring is a gauge, and gauges are out.

  Nothing here is invented. The engine payload proves steps, sleep hours and the
  HRV ratio; a row whose metric no stream filed today prints the voice.md
  unmeasured string and carries the Imputed chip, so a reader can tell a real
  zero from a number nobody took (R1).
*/

/**
 * The §1.8 stats, in the spec's order. `read` pulls from the engine payload's
 * own observations; anything it cannot prove returns null and the tile says so.
 * Calories are the clearest case: `active_energy` is a wearable metric the
 * backend can store, but it is not an engine observation, so Bryan does not
 * claim a kcal number until the loader lane forwards one.
 */
const STATS: ReadonlyArray<{
  key: string;
  label: string;
  unit: string;
  digits: number;
  read: (d: DashboardData) => number | null;
}> = [
  { key: "steps", label: "Steps", unit: "steps", digits: 0, read: (d) => obs(d, "steps") },
  { key: "active_energy", label: "Calories", unit: "kcal", digits: 0, read: () => null },
  { key: "strain", label: "Strain", unit: "0–21", digits: 1, read: () => null },
  { key: "resting_hr", label: "Resting heart rate", unit: "bpm", digits: 0, read: () => null },
  { key: "recovery_ratio", label: "HRV vs baseline", unit: "× baseline", digits: 2, read: (d) => obs(d, "recovery_ratio") },
  { key: "sleep_hours", label: "Sleep", unit: "h", digits: 1, read: (d) => obs(d, "sleep_hours") },
  { key: "sri", label: "Sleep regularity", unit: "SRI", digits: 0, read: (d) => obs(d, "sri") },
  { key: "spo2", label: "Blood oxygen", unit: "%", digits: 0, read: () => null },
  { key: "respiratory_rate", label: "Respiratory rate", unit: "br/min", digits: 1, read: () => null },
  { key: "skin_temp_dev", label: "Skin temperature", unit: "°C", digits: 1, read: () => null },
];

/** One engine observation, or null when the payload does not carry it. */
function obs(d: DashboardData, key: string): number | null {
  const value = d.observations[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The rows to draw: the loader lane's list when it supplies one, else what the payload proves. */
export function wearableStats(d: DashboardData): WearableStat[] {
  if (d.wearable !== undefined) return d.wearable;
  const wearable = d.source.mode === "live" && d.source.demo_mode !== true ? "whoop" : "seeded";
  return STATS.map((stat) => {
    const value = stat.read(d);
    return {
      key: stat.key,
      label: stat.label,
      value,
      unit: stat.unit,
      digits: stat.digits,
      provenance: provenanceOf(stat.key, value !== null, contextFor(stat.key, d.source.wearable_sources, wearable)),
    };
  });
}

const fmtStat = (stat: WearableStat): string =>
  stat.value === null ? "—" : stat.digits === 0 ? fmtInt(stat.value) : stat.value.toFixed(stat.digits);

function StatTile({ stat }: { stat: WearableStat }) {
  const missing = stat.value === null;
  return (
    <li
      className="flex min-w-0 flex-col gap-1 p-4"
      style={{ background: T.bg, borderRadius: 16 }}
      title={missing ? "Unmeasured today — scored at the population average, earns nothing." : undefined}
    >
      <span className="truncate text-sm" style={{ color: T.muted }}>
        {stat.label}
      </span>
      <span className="tnum font-extrabold leading-none" style={{ color: missing ? T.muted : T.ink, fontSize: 24 }}>
        {fmtStat(stat)}
        {!missing && (
          <span className="ml-1 text-sm font-medium" style={{ color: T.muted }}>
            {stat.unit}
          </span>
        )}
      </span>
      <span className="flex items-center">
        <ProvenanceChip source={stat.provenance} />
      </span>
    </li>
  );
}

export function WearableNumbers({ d }: { d: DashboardData }) {
  const [open, setOpen] = useState(false);
  const stats = wearableStats(d);

  return (
    <section
      id="wearable"
      aria-labelledby="wearable-title"
      className="min-w-0 p-5 md:p-8"
      style={{ background: T.surface, borderRadius: T.radius }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls="wearable-stats"
        className="flex min-h-11 w-full items-center gap-3 text-left"
      >
        <span className="min-w-0 flex-1">
          <span id="wearable-title" className="block font-bold leading-tight" style={{ color: T.ink, fontSize: 24 }}>
            The numbers your wearable already knows
          </span>
          <span className="mt-1 block text-sm" style={{ color: T.muted }}>
            Steps, strain, sleep stages, resting heart rate. Useful, not new.
          </span>
        </span>
        <span
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full"
          style={{ background: T.bg }}
        >
          <ChevronDown
            size={20}
            strokeWidth={2}
            aria-hidden="true"
            focusable="false"
            color={T.ink}
            style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 200ms ease" }}
          />
        </span>
      </button>
      <ul
        id="wearable-stats"
        hidden={!open}
        className="m-0 mt-5 grid list-none grid-cols-2 gap-3 p-0 sm:grid-cols-3 lg:grid-cols-5"
      >
        {stats.map((stat) => (
          <StatTile key={stat.key} stat={stat} />
        ))}
      </ul>
    </section>
  );
}

export default WearableNumbers;
