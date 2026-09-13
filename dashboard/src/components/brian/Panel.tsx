import type { ReactNode } from "react";
import { T } from "@/lib/tokens";
import { PROVENANCE_HINT, PROVENANCE_LABEL } from "@/lib/score/provenance";
import type { Provenance } from "@/lib/score/provenance";

export interface PanelProps {
  /** In-page anchor (`#today`, `#layers`, …). */
  id?: string;
  /** Id of the panel's h2, so the section has an accessible name. */
  labelledBy?: string;
  className?: string;
  children: ReactNode;
}

/**
 * Grey 20px-radius container — the only container style on the page. Padding is
 * the spec's 20 mobile / 32 desktop, which is what nested radii are measured
 * against (a tile inside a panel is 16, outer minus padding).
 */
export function Panel({ id, labelledBy, className = "", children }: PanelProps) {
  return (
    <section
      id={id}
      aria-labelledby={labelledBy}
      className={`min-w-0 scroll-mt-6 p-5 md:p-8 ${className}`}
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

/** Section title at 24px, with the subtitle the screens spec gives it. */
export function H2({ id, sub, children }: H2Props) {
  return (
    <div className="mb-5">
      <h2 id={id} className="m-0 font-bold leading-tight" style={{ color: T.ink, fontSize: 24 }}>
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

export interface ChipProps {
  children: ReactNode;
  /** Earn green, cost red, or the neutral surface-2 fill. */
  tone?: "earn" | "cost" | "neutral";
  title?: string;
}

/** 12px pill, 9999 radius. Colour only ever carries earn / cost (law 5). */
export function Chip({ children, tone = "neutral", title }: ChipProps) {
  const fill =
    tone === "earn"
      ? { background: T.earnSoft, color: T.earn }
      : tone === "cost"
        ? { background: T.costSoft, color: T.cost }
        : { background: T.surface2, color: T.muted };
  return (
    <span
      className="inline-flex shrink-0 items-center rounded-full px-2 py-0.5 font-medium whitespace-nowrap"
      style={{ ...fill, fontSize: 12 }}
      title={title}
    >
      {children}
    </span>
  );
}

/**
 * Where a number came from: Glasses · WHOOP · Entered · Seeded · Imputed. Every
 * metric on the page carries one — a value without it is a bug (law 3). Seeded
 * is never relabelled as live, and an unmeasured factor reads Imputed, which is
 * the chip that says the score counted it as nothing.
 */
export function ProvenanceChip({ source }: { source: Provenance }) {
  return <Chip title={PROVENANCE_HINT[source]}>{PROVENANCE_LABEL[source]}</Chip>;
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
