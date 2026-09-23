"use client";
import { useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useBackendUrl } from "@/lib/useBackendUrl";
import { T } from "@/lib/tokens";
import type { PinRow } from "@/lib/score/types";
import { Icon } from "./icons";
import { H2, Panel } from "./Panel";

/** screens.md §1.4: merged episodes, at most 12 on Today. */
const STRIP_LIMIT = 12;
const FRAME_W = 288;
const FRAME_H = 160;
/** One card plus its gap — the distance an arrow key moves the strip. */
const SCROLL_STEP = FRAME_W + 16;

function Pin({ p, fluid }: { p: PinRow; fluid: boolean }) {
  const earn = p.kind === "earn";
  const src = useBackendUrl(p.img);
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
            src={src}
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
        <div className="text-base font-semibold" style={{ color: T.ink }}>
          {p.seen}
        </div>
        <div className="mt-1 text-sm" style={{ color: T.text }}>
          {p.effect}
        </div>
        <div className="mt-2 text-xs" style={{ color: T.muted }}>
          Evidence grade {p.grade}
        </div>
      </figcaption>
    </figure>
  );
}

/**
 * What the glasses saw (screens.md §1.4) — the evidence pins, one per merged
 * episode. The engine merges fragments and repeated sightings before it pins
 * anything (`merge_episodes`), so a row is an episode and never a frame count:
 * "55 drinks" is a pipeline bug, never a display (SKILL.md "Do not").
 *
 * The strip is the one place on the page allowed to scroll sideways, so it
 * carries a visible scrollbar and arrow-key handling (checklist.md).
 */
export function Evidence({ pins }: { pins: readonly PinRow[] }) {
  const [full, setFull] = useState(false);
  const strip = useRef<HTMLUListElement | null>(null);
  const capped = !full && pins.length > STRIP_LIMIT;
  // Newest first: the frame from a minute ago is the one you look for.
  const shown = (capped ? pins.slice(-STRIP_LIMIT) : pins).slice().reverse();

  return (
    <Panel id="evidence" labelledBy="evidence-title">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <H2 id="evidence-title" sub="Frames stay on your phone. Each one is tied to the number it moved.">
          What the glasses saw
        </H2>
        {pins.length > STRIP_LIMIT && (
          <button
            type="button"
            onClick={() => setFull((v) => !v)}
            aria-pressed={full}
            className="tile inline-flex h-11 shrink-0 items-center gap-1 rounded-full px-4 text-sm font-medium"
            style={{ background: T.bg, color: T.ink, border: 0 }}
          >
            {full ? (
              <>
                <ChevronLeft size={16} aria-hidden="true" /> Back to strip
              </>
            ) : (
              <>
                Full Day <ChevronRight size={16} aria-hidden="true" />
              </>
            )}
          </button>
        )}
      </div>

      {pins.length === 0 ? (
        <p className="m-0 text-sm" style={{ color: T.muted }}>
          Put the glasses on. Bryan starts counting light, people, and air the moment the camera is up.
        </p>
      ) : full ? (
        <ul className="m-0 grid list-none grid-cols-1 gap-4 p-0 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {shown.map((p) => (
            <li key={p.id} className="min-w-0">
              <Pin p={p} fluid />
            </li>
          ))}
        </ul>
      ) : (
        <ul
          ref={strip}
          // The strip is focusable so the arrow keys below have somewhere to
          // land; a scroll container is not reachable by keyboard otherwise.
          tabIndex={0}
          aria-label="Evidence pins, newest first"
          onKeyDown={(e) => {
            const by = e.key === "ArrowRight" ? SCROLL_STEP : e.key === "ArrowLeft" ? -SCROLL_STEP : 0;
            if (by === 0) return;
            e.preventDefault();
            strip.current?.scrollBy({ left: by, behavior: "smooth" });
          }}
          className="strip -mx-1 my-0 flex list-none snap-x snap-mandatory gap-4 overflow-x-auto px-1 pb-2"
        >
          {shown.map((p) => (
            <li key={p.id} className="shrink-0 snap-start">
              <Pin p={p} fluid={false} />
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export default Evidence;
