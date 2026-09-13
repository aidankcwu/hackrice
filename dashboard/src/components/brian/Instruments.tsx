"use client";
import { useState } from "react";
import { Brain, Clock, Sun, Trees, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { T } from "@/lib/tokens";
import {
  chipLabel,
  instrumentTiles,
  type InstrumentIcon,
  type InstrumentSource,
  type InstrumentTile,
  type Status,
} from "@/lib/score/instruments";
import { PROVENANCE_HINT } from "@/lib/score/provenance";
import { H2, Panel } from "./Panel";
import { InstrumentSheet } from "./InstrumentSheet";
import { Sparkline } from "./Sparkline";

/**
 * "What only your glasses can see" — the five instrument tiles (screens.md §1.2).
 * This is the product: one component, five configs, and no per-tile layout code.
 *
 * Every tile renders on every day. When nothing measured a tile's number it
 * shows the voice.md unmeasured line and the payload's own reason in place of
 * the number, because an instrument that vanishes on a thin day is the same lie
 * as an invented reading (R1).
 */

/** SKILL.md fixes one icon per layer; these are those five. */
const TILE_ICONS: Readonly<Record<InstrumentIcon, LucideIcon>> = {
  clock: Clock,
  sun: Sun,
  users: Users,
  trees: Trees,
  brain: Brain,
};

/** screens.md: green at/above target, amber within 30 %, red below, grey unmeasured. */
const DOT: Readonly<Record<Status, string>> = {
  good: T.earn,
  near: "#B45309",
  poor: T.cost,
  unmeasured: T.surface2,
};

const DOT_TITLE: Readonly<Record<Status, string>> = {
  good: "At or above target",
  near: "Within 30 % of target",
  poor: "Below target",
  unmeasured: "Unmeasured today",
};

function Tile({ tile, onOpen }: { tile: InstrumentTile; onOpen: () => void }) {
  const Glyph = TILE_ICONS[tile.icon];
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-haspopup="dialog"
      // 44 px is the floor, not the height: the tile is a full card and the
      // whole card is the target.
      className="tile flex w-full min-h-[11rem] min-w-0 flex-col items-start gap-3 rounded-2xl p-4 text-left"
      style={{ background: T.bg }}
    >
      <div className="flex w-full items-center gap-2">
        <Glyph size={20} strokeWidth={2} color={tile.accent ?? T.ink} aria-hidden="true" focusable="false" />
        <span className="min-w-0 flex-1 truncate text-base font-bold" style={{ color: T.ink }}>
          {tile.title}
        </span>
        <span
          className="h-2 w-2 shrink-0 rounded-full"
          style={{ background: DOT[tile.status] }}
          title={DOT_TITLE[tile.status]}
          aria-hidden="true"
        />
      </div>

      {tile.number === null ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          {tile.unmeasuredNote}
        </p>
      ) : (
        <p
          className="tnum m-0 font-extrabold leading-none tracking-tight"
          style={{ fontSize: 40, color: tile.accent ?? T.ink }}
        >
          {tile.number}
        </p>
      )}

      <p className="m-0 line-clamp-3 flex-1 text-sm" style={{ color: T.muted }}>
        {tile.number === null ? tile.reason : tile.reading}
      </p>

      <div className="flex w-full items-end justify-between gap-2">
        <span
          className="text-xs whitespace-nowrap"
          style={{ color: T.muted }}
          title={PROVENANCE_HINT[tile.chip]}
        >
          {chipLabel(tile.chip)}
        </span>
        <Sparkline values={tile.series} label={`${tile.title}: ${tile.seriesLabel}`} color={tile.accent} />
      </div>
    </button>
  );
}

export interface InstrumentsProps {
  source: InstrumentSource;
}

export function Instruments({ source }: InstrumentsProps) {
  const tiles = instrumentTiles(source);
  const [openKey, setOpenKey] = useState<string | null>(null);
  return (
    <Panel labelledBy="instruments-title" className="md:col-span-7">
      <H2
        id="instruments-title"
        sub="Five layers no wearable measures. All from the camera and the mic, on your phone."
      >
        What only your glasses can see
      </H2>
      <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-2 xl:grid-cols-5">
        {tiles.map((tile) => (
          <li key={tile.key} className="flex min-w-0">
            <Tile tile={tile} onOpen={() => setOpenKey(tile.key)} />
            <InstrumentSheet tile={tile} open={openKey === tile.key} onClose={() => setOpenKey(null)} />
          </li>
        ))}
      </ul>
    </Panel>
  );
}
