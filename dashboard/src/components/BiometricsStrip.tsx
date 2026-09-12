"use client";
import { api } from "@/lib/api";
import type { BiometricSeries } from "@/lib/types";
import { usePoll } from "@/lib/usePoll";

/** The wearable's own line: intraday HR, with the trigger threshold drawn in,
 * plus a tile per remaining SPEC §14.1 metric.
 *
 * SPEC §14.3 fires `biometric_anomaly` on HR above resting × 1.4 while the
 * wearer is not exercising, so the dashed line is exactly the line the gate
 * watches — the spike crossing it is the escalation, on screen.
 *
 * Every tile carries its own origin pill. `seeded` is the hardcoded demo day;
 * `live` means a real device is pushing into `/api/wearables/ingest` right now
 * and its rows have taken over that window (see `docs/WEARABLES.md`).
 */
const WINDOW_S = 600;
const RATIO = 1.4;
const W = 600;
const H = 40;

type Tile = {
  metric: string;
  label: string;
  /** Latest sample -> the big number. `sum` tiles total the window instead. */
  format: (value: number) => string;
  sum?: boolean;
};

const TILES: Tile[] = [
  { metric: "hrv_rmssd", label: "HRV", format: (v) => `${Math.round(v)} ms` },
  { metric: "spo2", label: "SpO2", format: (v) => `${Math.round(v)}%` },
  { metric: "respiratory_rate", label: "RR", format: (v) => `${Math.round(v)} brpm` },
  { metric: "wrist_temp_dev", label: "Wrist temp", format: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}°C` },
  { metric: "strain", label: "Strain", format: (v) => v.toFixed(1) },
  { metric: "steps_delta", label: "Steps / 10 min", format: (v) => String(Math.round(v)), sum: true },
  { metric: "env_sound_db", label: "Sound", format: (v) => `${Math.round(v)} dB` },
];
const TILE_METRICS = TILES.map((t) => t.metric);

/** A 10-minute sparkline. Flat or empty series render as a baseline rule. */
function Spark({ points }: { points: [number, number][] }) {
  const w = 64;
  const h = 16;
  if (points.length < 2) return <svg viewBox={`0 0 ${w} ${h}`} className="h-4 w-16" />;
  const ts = points.map(([t]) => t);
  const vs = points.map(([, v]) => v);
  const t0 = Math.min(...ts);
  const span = Math.max(1, Math.max(...ts) - t0);
  const lo = Math.min(...vs);
  const hi = Math.max(...vs);
  const line = points
    .map(([t, v]) => `${(((t - t0) / span) * w).toFixed(1)},${(h - ((v - lo) / (hi - lo || 1)) * (h - 2) - 1).toFixed(1)}`)
    .join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-4 w-16">
      <polyline points={line} fill="none" stroke="currentColor" className="text-sky-400/80"
                strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function OriginPill({ origin }: { origin: string }) {
  const live = origin === "live";
  return (
    <span className={`rounded px-1 py-px text-[9px] font-semibold uppercase tracking-wider ${
      live ? "bg-emerald-500/20 text-emerald-300" : "bg-zinc-700/60 text-zinc-400"}`}>
      {live ? "live" : "seeded"}
    </span>
  );
}

function MetricTile({ tile, series }: { tile: Tile; series?: BiometricSeries }) {
  const points = series?.points ?? [];
  const last = points.at(-1)?.[0] ?? 0;
  const shown = points.filter(([t]) => t >= last - WINDOW_S);
  const value = tile.sum
    ? shown.reduce((total, [, v]) => total + v, 0)
    : shown.at(-1)?.[1];
  return (
    <div className="min-w-[118px] flex-1 rounded-md border border-zinc-800 bg-zinc-900/40 px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wider text-zinc-500">{tile.label}</span>
        <OriginPill origin={series?.origin ?? "seed"} />
      </div>
      <div className="mt-0.5 flex items-end justify-between gap-2">
        <span className="text-sm font-semibold text-white">
          {shown.length ? tile.format(value as number) : "—"}
        </span>
        <Spark points={shown} />
      </div>
      <div className="muted truncate text-[10px]">{series?.source || "no device"}</div>
    </div>
  );
}

export function BiometricsStrip({ restingFallback = 58 }: { restingFallback?: number }) {
  const biometrics = usePoll(() => api.biometrics("heart_rate"), 5000);
  const others = usePoll(() => api.biometricsMulti(TILE_METRICS), 5000);
  const wearables = usePoll(() => api.wearablesStatus(), 15000);
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

  const live = wearables.data?.live_connected ?? false;
  const devices = wearables.data?.live_devices ?? [];
  const connection = live
    ? `live: ${devices.join(", ") || "wearable"}`
    : "seeded";

  return (
    <section className="panel p-4">
      <div className="section-title">
        <span>Wearable</span>
        <span className="muted">
          {biometrics.data?.source || "wearable"} · last 10 min · {connection}
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
      <div className="mt-3 flex flex-wrap gap-2">
        {TILES.map((tile) => (
          <MetricTile key={tile.metric} tile={tile} series={others.data?.series?.[tile.metric]} />
        ))}
      </div>
    </section>
  );
}
