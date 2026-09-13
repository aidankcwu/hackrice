"use client";
import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { T } from "@/lib/tokens";
import type { PinRow } from "@/lib/score/types";
import { DecisionStream } from "./DecisionStream";
import { Icon } from "./icons";
import { Panel } from "./Panel";

/** The strip stays light; the full-day grid is the place for a long day. */
const STRIP_LIMIT = 24;
const FRAME_W = 288;
const FRAME_H = 160;

function Pin({ p, fluid }: { p: PinRow; fluid: boolean }) {
  const earn = p.kind === "earn";
  return (
    <figure
      className={`${fluid ? "w-full" : "w-72 shrink-0"} m-0 overflow-hidden`}
      style={{ background: T.bg, borderRadius: T.radiusPin, border: `1px solid ${T.line}` }}
    >
      <div className="relative h-40" style={{ background: T.surface }}>
        {p.img ? (
          // Evidence frames are served by the local backend and are already
          // JPEG-sized; next/image would need a remotePatterns entry for a
          // host that changes per machine, so the plain element is used.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={p.img}
            alt={p.seen}
            width={FRAME_W}
            height={FRAME_H}
            loading="lazy"
            decoding="async"
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center">
            <Icon name={p.icon} size={28} strokeWidth={1.75} color={T.muted} />
          </div>
        )}
        <span
          className="tnum absolute left-3 top-3 rounded-full px-2.5 py-1 text-xs font-semibold"
          style={{ background: T.header, color: T.bg }}
        >
          {p.time}
        </span>
        {/* Ink on the soft fill: the tinted green text of the reference sits at 4.47:1, just under 4.5. */}
        <span
          className="absolute right-3 top-3 rounded-full px-2.5 py-1 text-xs font-semibold"
          style={{ background: earn ? T.earnSoft : T.costSoft, color: T.ink }}
        >
          {earn ? "Earned" : "Cost"}
        </span>
      </div>
      <figcaption className="p-4">
        <div className="text-sm font-semibold" style={{ color: T.ink }}>
          {p.seen}
        </div>
        <div className="mt-1 text-sm" style={{ color: T.text }}>
          {p.effect}
        </div>
        <div className="mt-2 text-sm" style={{ color: T.muted }}>
          Evidence grade {p.grade}
        </div>
      </figcaption>
    </figure>
  );
}

function EvidenceStrip({ pins }: { pins: PinRow[] }) {
  const [full, setFull] = useState(false);
  const capped = !full && pins.length > STRIP_LIMIT;
  const shown = capped ? pins.slice(-STRIP_LIMIT) : pins;

  return (
    <Panel id="evidence" labelledBy="evidence-title">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 id="evidence-title" className="m-0 text-xl font-bold leading-tight" style={{ color: T.ink }}>
            What the glasses saw, and what it cost
          </h2>
          <p className="m-0 mt-1 text-sm" style={{ color: T.muted }}>
            Frames stay on your phone. Each one is tied to the number it moved.
          </p>
        </div>
        {pins.length > 0 && (
          <button
            type="button"
            onClick={() => setFull((v) => !v)}
            aria-pressed={full}
            className="tile inline-flex h-11 items-center gap-1 rounded-full px-4 text-sm font-medium"
            style={{ background: T.bg, color: T.ink, border: 0 }}
          >
            {full ? (
              <>
                <ChevronLeft size={16} aria-hidden="true" /> Back to strip
              </>
            ) : (
              <>
                Full day <ChevronRight size={16} aria-hidden="true" />
              </>
            )}
          </button>
        )}
      </div>

      {pins.length === 0 ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          Nothing pinned yet. A pin appears when the glasses see something that moves a number.
        </p>
      ) : full ? (
        <ul className="m-0 grid list-none grid-cols-1 gap-4 p-0 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {pins.map((p) => (
            <li key={p.id} className="min-w-0">
              <Pin p={p} fluid />
            </li>
          ))}
        </ul>
      ) : (
        <>
          <ul className="strip -mx-1 my-0 flex list-none gap-4 overflow-x-auto px-1 pb-2">
            {shown.map((p) => (
              <li key={p.id} className="shrink-0">
                <Pin p={p} fluid={false} />
              </li>
            ))}
          </ul>
          {capped && (
            <p className="m-0 mt-2 text-sm" style={{ color: T.muted }}>
              Showing the last {STRIP_LIMIT} of {pins.length}. Full day shows them all.
            </p>
          )}
        </>
      )}
    </Panel>
  );
}

/**
 * The evidence strip and, directly beneath it, the decision stream — what the
 * glasses saw, then what the reasoner did about it. The stream polls
 * `GET /api/decisions` itself but takes the pins it links to from this payload.
 */
export function Evidence({ pins }: { pins: PinRow[] }) {
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <EvidenceStrip pins={pins} />
      <DecisionStream pins={pins} />
    </div>
  );
}
