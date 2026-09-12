"use client";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

/** The wearable's own line: intraday HR, with the trigger threshold drawn in.
 *
 * SPEC §14.3 fires `biometric_anomaly` on HR above resting × 1.4 while the
 * wearer is not exercising, so the dashed line is exactly the line the gate
 * watches — the spike crossing it is the escalation, on screen.
 */
const WINDOW_S = 600;
const RATIO = 1.4;
const W = 600;
const H = 40;

export function BiometricsStrip({ restingFallback = 58 }: { restingFallback?: number }) {
  const biometrics = usePoll(() => api.biometrics("heart_rate"), 5000);
  const seeded = usePoll(() => api.seeded(7), 30000);
  const points = biometrics.data?.points ?? [];
  const resting = seeded.data?.at(-1)?.resting_hr ?? restingFallback;
  const threshold = resting * RATIO;

  const last = points.at(-1)?.[0] ?? 0;
  const shown = points.filter(([t]) => t >= last - WINDOW_S);
  const values = shown.map(([, v]) => v);
  const lo = Math.min(threshold - 6, ...(values.length ? values : [resting]));
  const hi = Math.max(threshold + 6, ...(values.length ? values : [resting]));
  const x = (t: number) => ((t - (last - WINDOW_S)) / WINDOW_S) * W;
  const y = (v: number) => H - ((v - lo) / (hi - lo || 1)) * H;
  const line = shown.map(([t, v]) => `${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const current = values.at(-1);
  const hot = current !== undefined && current > threshold;

  return (
    <section className="panel p-4">
      <div className="section-title">
        <span>Heart rate</span>
        <span className="muted">
          {biometrics.data?.source || "wearable"} · last 10 min
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-10 w-full">
        <line
          x1={0}
          x2={W}
          y1={y(threshold)}
          y2={y(threshold)}
          stroke="currentColor"
          className="text-amber-500/60"
          strokeWidth={1}
          strokeDasharray="4 4"
          vectorEffect="non-scaling-stroke"
        />
        {shown.length > 1 && (
          <polyline
            points={line}
            fill="none"
            stroke="currentColor"
            className={hot ? "text-rose-400" : "text-emerald-400"}
            strokeWidth={1.5}
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>
      <div className="mt-2 flex flex-wrap gap-x-5 text-xs text-zinc-300">
        <span>
          now <em className={hot ? "text-rose-300" : "text-white"}>{current !== undefined ? `${Math.round(current)} bpm` : "—"}</em>
        </span>
        <span>resting <em>{Math.round(resting)}</em></span>
        <span>trigger <em>&gt; {Math.round(threshold)} bpm while not exercising</em></span>
      </div>
    </section>
  );
}
