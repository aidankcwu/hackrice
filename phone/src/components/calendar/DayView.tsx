import { Check, Syringe, Utensils, X } from "lucide-react";
import type { CSSProperties } from "react";
import { LANE_COUNT, type Band, type DayHeader, type Frame, type Item } from "@/lib/calendar";
import { clock } from "@/lib/rules";
import type { Tone } from "@/lib/rules";
import { GLYPHS } from "./glyphs";

const LANE_W = 12;
const LANE_GAP = 2;
const RAILS_W = LANE_COUNT * LANE_W + (LANE_COUNT - 1) * LANE_GAP;
const HOURS = Array.from({ length: 24 }, (_, h) => h);

/** The two ceilings, last night, streaks, weather and air, what was held back. */
export function DayHeaderView({ header }: { header: DayHeader }) {
  const badAir = header.aqi > 100;
  return (
    <section aria-label="The day" className="rounded-panel bg-surface p-panel">
      <p className="type-title m-0 text-ink">{header.title}</p>
      <p className="type-number m-0 mt-2 text-ink tabular-nums">
        Cognition {Math.round(header.cognition)}% <span className="text-muted">·</span> Body {Math.round(header.body)}%
      </p>
      {header.recovery ? <p className="type-secondary m-0 mt-1 text-muted">Recovery day. The protocol is relaxed.</p> : null}
      <p className="type-secondary m-0 mt-2 text-text tabular-nums">Last night {header.sleep}</p>
      <p className="type-secondary m-0 mt-1 text-text tabular-nums">{header.streaks.join(" · ")}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Chip>{header.weather}</Chip>
        <Chip tone={badAir ? "watch" : undefined}>AQI {header.aqi}</Chip>
        <Chip>Held back {header.heldBack}</Chip>
        <Chip>Seeded</Chip>
      </div>
    </section>
  );
}

function Chip({ children, tone }: { children: React.ReactNode; tone?: "watch" }) {
  return (
    <span
      className={`type-chip inline-flex min-h-6 items-center rounded-full px-2 tabular-nums ${
        tone === "watch" ? "bg-watch-soft text-watch" : "bg-surface-2 text-text"
      }`}
    >
      {children}
    </span>
  );
}

/**
 * The day on a vertical 24-hour timeline. Each hour is a row in normal flow: its
 * items stack, so a busy hour grows and the bands stretch with it (each band is
 * drawn per row as the part of it inside that hour). Night hours are short.
 */
export function DayTimeline({ bands, items, frames, sick }: { bands: Band[]; items: Item[]; frames: Frame[]; sick: boolean }) {
  return (
    <div aria-label="Timeline" className={sick ? "opacity-80" : undefined}>
      {HOURS.map((h) => {
        const from = h * 60;
        const to = from + 60;
        const rowItems = items.filter((i) => i.time >= from && i.time < to);
        const rowFrames = frames.filter((f) => f.start < to && f.end > from);
        const startingFrames = frames.filter((f) => f.start >= from && f.start < to);
        return (
          <div
            key={h}
            className="grid"
            style={{
              gridTemplateColumns: `28px ${RAILS_W}px minmax(0, 1fr)`,
              columnGap: 8,
              minHeight: h < 6 ? 26 : 44,
            }}
          >
            <span className="type-caption -mt-[7px] text-right text-muted tabular-nums">{String(h).padStart(2, "0")}</span>
            <div className="relative">
              {bands.map((band) => (
                <BandSlice key={band.id} band={band} from={from} to={to} />
              ))}
            </div>
            <div className="relative border-t-[0.5px] border-line">
              {rowFrames.map((frame) => (
                <span
                  key={frame.id}
                  aria-hidden="true"
                  className="absolute inset-x-0 rounded-[8px] bg-surface"
                  style={slice(frame.start, frame.end, from, to)}
                />
              ))}
              <div className="relative">
                {startingFrames.map((frame) => (
                  <p key={frame.id} className="type-caption m-0 px-2 pt-1 text-muted">
                    {frame.label}, {clock(frame.start)} to {clock(frame.end)}
                  </p>
                ))}
                {rowItems.map((item) => (
                  <ItemRow key={item.id} item={item} />
                ))}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** top/bottom percentages for the part of [start, end) inside [from, to). */
function slice(start: number, end: number, from: number, to: number): CSSProperties {
  const a = Math.max(start, from);
  const b = Math.min(end, to);
  return { top: `${((a - from) / 60) * 100}%`, bottom: `${((to - b) / 60) * 100}%` };
}

function BandSlice({ band, from, to }: { band: Band; from: number; to: number }) {
  if (band.start >= to || band.end <= from) return null;
  const left = band.lane * (LANE_W + LANE_GAP);
  const first = band.start >= from;
  const last = band.end <= to;
  const radius = `${first ? 6 : 0}px ${first ? 6 : 0}px ${last ? 6 : 0}px ${last ? 6 : 0}px`;
  const filledEnd = band.progress !== undefined ? band.start + band.progress * (band.end - band.start) : band.start;
  const ticks = (band.ticks ?? []).filter((t) => t >= from && t < to);
  return (
    <div className="absolute" style={{ left, width: LANE_W, ...slice(band.start, band.end, from, to) }}>
      <span
        aria-hidden="true"
        className={`absolute inset-0 ${band.dim ? "bg-band opacity-60" : "bg-band"}`}
        style={{ borderRadius: radius }}
      />
      {band.progress !== undefined && filledEnd > Math.max(band.start, from) ? (
        <span
          aria-hidden="true"
          className="absolute inset-x-0 bg-earn-soft"
          style={{
            top: 0,
            height: `${((Math.min(filledEnd, to) - Math.max(band.start, from)) / (Math.min(band.end, to) - Math.max(band.start, from))) * 100}%`,
            borderRadius: radius,
          }}
        />
      ) : null}
      {ticks.map((t) => (
        <span
          key={t}
          aria-hidden="true"
          className="absolute inset-x-[2px] h-[2px] rounded-full bg-earn"
          style={{ top: `${((t - Math.max(band.start, from)) / (Math.min(band.end, to) - Math.max(band.start, from))) * 100}%` }}
        />
      ))}
      {first ? (
        <span
          className="absolute top-1 left-0 z-10 w-full overflow-visible text-[10px] leading-none font-semibold whitespace-nowrap text-muted"
          style={{ writingMode: "vertical-rl" }}
        >
          {band.label}
        </span>
      ) : null}
    </div>
  );
}

const MARKER: Record<Tone, string> = {
  violation: "bg-cost-soft text-cost",
  watch: "bg-watch-soft text-watch",
  inside: "text-earn",
  neutral: "text-muted",
};
const LINE: Record<Tone, string> = { violation: "text-cost", watch: "text-watch", inside: "text-earn", neutral: "text-muted" };

function ItemRow({ item }: { item: Item }) {
  if (item.line) {
    return (
      <div className="flex items-center gap-2 px-2 py-1">
        <span aria-hidden="true" className="h-px flex-1 bg-line" />
        <span className="type-caption text-muted tabular-nums">{item.title}</span>
      </div>
    );
  }
  const Icon = GLYPHS[item.glyph];
  return (
    <div className="flex gap-2 px-1 py-1.5">
      <span className={`mt-px grid size-[22px] shrink-0 place-items-center rounded-full ${MARKER[item.tone]}`}>
        <Icon size={14} strokeWidth={2} aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p className={`m-0 flex items-baseline gap-2 ${item.quiet ? "type-caption text-muted" : "type-secondary text-text"}`}>
          <span className="shrink-0 text-muted tabular-nums">{clock(item.time)}</span>
          <span className="min-w-0">{item.title}</span>
          {item.mark === "taken" ? <Check size={14} strokeWidth={2.5} className="shrink-0 self-center text-earn" aria-label="Taken" /> : null}
          {item.mark === "missed" ? <X size={14} strokeWidth={2.5} className="shrink-0 self-center text-cost" aria-label="Missed" /> : null}
        </p>
        {item.detail ? <p className="type-caption m-0 text-muted">{item.detail}</p> : null}
        {item.lines.map((line) => (
          <p key={line} className={`type-caption m-0 mt-0.5 ${LINE[item.tone]}`}>
            {line}
          </p>
        ))}
      </div>
      {item.thumb ? (
        <span className="grid size-9 shrink-0 place-items-center rounded-[8px] bg-surface-2 text-muted" aria-label="Frame from the glasses, seeded">
          {item.glyph === "peptide" ? <Syringe size={16} strokeWidth={2} aria-hidden="true" /> : <Utensils size={16} strokeWidth={2} aria-hidden="true" />}
        </span>
      ) : null}
    </div>
  );
}
