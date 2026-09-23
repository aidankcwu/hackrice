export interface ProgressBarProps {
  /** 0..1, clamped. */
  value: number;
  tone?: "good" | "ink";
  /** The accessible name: what the bar measures. */
  label: string;
}

/** A 6 px track on --band with a pill fill. */
export function ProgressBar({ value, tone = "good", label }: ProgressBarProps) {
  const fraction = Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
  const percent = Math.round(fraction * 100);
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      aria-valuetext={`${percent}%`}
      className="h-1.5 w-full overflow-hidden rounded-full bg-band"
    >
      <div
        className={`h-full rounded-full transition-[width] duration-200 ease-out ${tone === "ink" ? "bg-ink" : "bg-good"}`}
        style={{ width: `${fraction * 100}%` }}
      />
    </div>
  );
}
