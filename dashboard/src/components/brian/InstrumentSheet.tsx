"use client";
import { useEffect, useRef } from "react";
import { T } from "@/lib/tokens";
import { chipLabel, type InstrumentTile } from "@/lib/score/instruments";
import { PROVENANCE_HINT } from "@/lib/score/provenance";
import { Sparkline } from "./Sparkline";

export interface InstrumentSheetProps {
  tile: InstrumentTile;
  open: boolean;
  onClose: () => void;
}

/**
 * The detail sheet behind one instrument tile (screens.md §1.2, the "Detail
 * sheet" column). A native `<dialog>`, so the browser supplies the focus trap,
 * Escape, and the inert backdrop rather than this file re-implementing them.
 *
 * Every row is already formatted by `instruments.ts`; nothing is computed here.
 * A row whose part of the tile is itself unmeasured reads as a dash, so the
 * sheet shows the shape of the instrument even on a day that measured little.
 */
export function InstrumentSheet({ tile, open, onClose }: InstrumentSheetProps) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  const titleId = `instrument-${tile.key}-title`;

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      // The backdrop is the dialog's own padding area; a click there closes,
      // a click on the card inside does not.
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      className="m-auto w-[min(34rem,calc(100vw-2rem))] max-w-none border-0 p-0 backdrop:bg-black/30"
      style={{ background: "transparent" }}
    >
      <div
        className="max-h-[85dvh] overflow-y-auto p-6 md:p-8"
        style={{ background: T.surface, borderRadius: T.radius, color: T.text }}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2
              id={titleId}
              className="m-0 text-xl font-bold leading-tight"
              style={{ color: tile.accent ?? T.ink }}
            >
              {tile.title}
            </h2>
            <p className="m-0 mt-1 text-sm" style={{ color: T.muted }}>
              {tile.measured ? tile.reading : tile.unmeasuredNote}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="tile -mr-2 -mt-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-base font-medium"
            style={{ background: T.bg, color: T.ink }}
          >
            <span aria-hidden="true">✕</span>
            <span className="sr-only">Close {tile.title}</span>
          </button>
        </div>

        <div className="mt-5 flex items-end justify-between gap-4">
          <p
            className="tnum m-0 font-extrabold leading-none"
            style={{ fontSize: 40, color: tile.number === null ? T.muted : tile.accent ?? T.ink }}
          >
            {tile.number ?? "—"}
          </p>
          <Sparkline values={tile.series} label={tile.seriesLabel} color={tile.accent} />
        </div>

        {!tile.measured && (
          <p className="m-0 mt-3 text-sm" style={{ color: T.muted }}>
            {tile.reason}
          </p>
        )}

        <dl className="m-0 mt-6 flex flex-col">
          {tile.detail.map((d) => (
            <div
              key={d.label}
              className="flex items-baseline justify-between gap-4 py-3"
              style={{ borderTop: `1px solid ${T.line}` }}
            >
              <dt className="min-w-0 text-sm" style={{ color: T.muted }}>
                {d.label}
              </dt>
              <dd className="tnum m-0 max-w-[60%] text-right text-sm font-medium" style={{ color: T.ink }}>
                {d.value}
              </dd>
            </div>
          ))}
        </dl>

        <div
          className="mt-5 flex flex-wrap items-center justify-between gap-3 pt-5"
          style={{ borderTop: `1px solid ${T.line}` }}
        >
          <span className="text-xs" style={{ color: T.muted }} title={PROVENANCE_HINT[tile.chip]}>
            {chipLabel(tile.chip)} · {tile.evidence}
          </span>
          {tile.action && (
            <a
              href={tile.action.href}
              className="tile inline-flex h-11 items-center rounded-full px-5 text-sm font-semibold"
              style={{ background: T.bg, color: T.ink }}
            >
              {tile.action.label}
            </a>
          )}
        </div>
      </div>
    </dialog>
  );
}
