import { T } from "@/lib/tokens";
import { sparkPath } from "@/lib/score/instruments";

export interface SparklineProps {
  /** Oldest first. Fewer than 7 points simply draws a shorter run; empty draws nothing. */
  values: readonly number[];
  /** What the run plots, read out in place of the picture. */
  label: string;
  width?: number;
  height?: number;
  /** Overrides the ink line — only the clock instrument does (one accent, one meaning). */
  color?: string;
}

/**
 * The 7-day run under an instrument number: 96 × 24, one ink line, no axes, no
 * grid, no fill (screens.md §1.2). It carries a trend, not a value — every
 * number it implies is already written above it — so the whole thing is one
 * labelled image rather than a chart assistive tech has to walk.
 *
 * A day the glasses never covered contributes no point at all; the line is
 * shorter, and nothing is interpolated across the gap (R1).
 */
export function Sparkline({ values, label, width = 96, height = 24, color }: SparklineProps) {
  const { d, last } = sparkPath(values, width, height);
  const stroke = color ?? T.ink;
  if (d === null && last === null) {
    return (
      <div
        className="shrink-0"
        style={{ width, height }}
        role="img"
        aria-label={`${label}: no days measured yet`}
      />
    );
  }
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="shrink-0 overflow-visible"
      role="img"
      aria-label={`${label}: ${values.length} ${values.length === 1 ? "day" : "days"} measured`}
    >
      {d !== null && (
        <path
          d={d}
          fill="none"
          stroke={stroke}
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
      {last !== null && <circle cx={last.x} cy={last.y} r={2} fill={stroke} />}
    </svg>
  );
}
