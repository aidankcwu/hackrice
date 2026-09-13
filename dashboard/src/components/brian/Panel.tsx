import type { ReactNode } from "react";
import { T } from "@/lib/tokens";

export interface PanelProps {
  /** In-page anchor (`#today`, `#week`, …). */
  id?: string;
  /** Id of the panel's h2, so the section has an accessible name. */
  labelledBy?: string;
  className?: string;
  children: ReactNode;
}

/** Grey 20px-radius container — the only container style on the page. */
export function Panel({ id, labelledBy, className = "", children }: PanelProps) {
  return (
    <section
      id={id}
      aria-labelledby={labelledBy}
      className={`min-w-0 p-6 md:p-8 scroll-mt-6 ${className}`}
      style={{ background: T.surface, borderRadius: T.radius }}
    >
      {children}
    </section>
  );
}

export interface H2Props {
  id?: string;
  sub?: string;
  children: ReactNode;
}

export function H2({ id, sub, children }: H2Props) {
  return (
    <div className="mb-5">
      <h2 id={id} className="m-0 text-xl font-bold leading-tight" style={{ color: T.ink }}>
        {children}
      </h2>
      {sub && (
        <p className="m-0 mt-1 text-sm" style={{ color: T.muted }}>
          {sub}
        </p>
      )}
    </div>
  );
}

export interface TrackProps {
  value: number;
  max?: number;
  color: string;
  /** Accessible name — the bar is the only place the value is drawn, not read. */
  label: string;
}

export function Track({ value, max = 100, color, label }: TrackProps) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div
      className="h-2 w-full rounded-full"
      style={{ background: T.surface2 }}
      role="progressbar"
      aria-label={label}
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={max}
    >
      <div className="h-2 rounded-full" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}
