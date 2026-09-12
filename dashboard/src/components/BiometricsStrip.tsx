"use client";
import { api } from "@/lib/api";
import type { BiometricSeries, SeededMetricRow } from "@/lib/types";
import { usePoll } from "@/lib/usePoll";

/** The wearable's own line: intraday HR, with the trigger threshold drawn in,
 * plus a tile per remaining SPEC §14.1 metric.
 *
 * SPEC §14.3 fires `biometric_anomaly` on HR above resting × 1.4 while the
 * wearer is not exercising, so the dashed line is exactly the line the gate
 * watches — the spike crossing it is the escalation, on screen.
 *
 * Every tile carries its own origin pill, and the pill is the honest part.
 * A real wrist device does not stream every metric at every cadence: a Fitbit
 * pushes intraday heart rate and steps continuously, files HRV, SpO2,
 * respiratory rate, skin temperature, resting HR and the sleep block once a
 * day (measured overnight, sometimes dated to the night before), and never
 * measures WHOOP's strain or an Apple Watch's ambient sound at all. So a tile
 * resolves in four steps, and each one gets a different pill:
 *
 *   1. a live intraday sample inside the 10-minute window   -> `live`
 *   2. else a daily row from the connected device           -> `live · today`
 *                                                              `live · last night`
 *   3. else, device connected but does not measure this     -> `not on fitbit`
 *   4. else (nothing connected) the seeded demo day         -> `seeded`
 *
 * Step 3 is the one that matters: with a device connected, a seeded number is
 * never shown in its place, because a demo-day strain figure sitting next to
 * real Fitbit numbers reads as if the whole strip were fake.
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
  /** The `/api/seeded` metric to fall back to when the device files this one
   * as a daily roll-up rather than an intraday series. */
  daily?: string;
  /** Label and format for that daily row, when it says something different —
   * `skin_temp_c` is an absolute temperature, `wrist_temp_dev` a deviation. */
  dailyLabel?: string;
  dailyFormat?: (value: number) => string;
  /** Measured while asleep: the row describes last night whichever day it is
   * filed under, so it is never labelled "today". */
  overnight?: boolean;
  /** No intraday series exists at all; render only when the daily row does. */
  dailyOnly?: boolean;
};

const TILES: Tile[] = [
  { metric: "hrv_rmssd", label: "HRV", format: (v) => `${Math.round(v)} ms`, daily: "hrv_rmssd_ms", overnight: true },
  { metric: "spo2", label: "SpO2", format: (v) => `${Math.round(v)}%`, daily: "spo2", overnight: true },
  { metric: "respiratory_rate", label: "RR", format: (v) => `${Math.round(v)} brpm`, daily: "respiratory_rate", overnight: true },
  { metric: "wrist_temp_dev", label: "Wrist temp Δ", format: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}°C`,
    daily: "skin_temp_c", dailyLabel: "Skin temp", dailyFormat: (v) => `${v.toFixed(1)}°C`, overnight: true },
  { metric: "resting_hr", label: "Resting HR", format: (v) => `${Math.round(v)} bpm`, daily: "resting_hr", overnight: true, dailyOnly: true },
  { metric: "sleep", label: "Sleep", format: (v) => `${v.toFixed(1)} h`, daily: "sleep_hours", overnight: true, dailyOnly: true },
  { metric: "strain", label: "Strain", format: (v) => v.toFixed(1) },
  { metric: "steps_delta", label: "Steps / 10 min", format: (v) => String(Math.round(v)), sum: true,
    daily: "steps", dailyLabel: "Steps / day", dailyFormat: (v) => Math.round(v).toLocaleString() },
  { metric: "env_sound_db", label: "Sound", format: (v) => `${Math.round(v)} dB` },
];
/** Only the tiles that have an intraday series worth asking `/api/biometrics` for. */
const TILE_METRICS = TILES.filter((t) => !t.dailyOnly).map((t) => t.metric);

const isoDay = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const shiftDay = (day: string, delta: number) => {
  const [y, m, d] = day.split("-").map(Number);
  return isoDay(new Date(y, m - 1, d + delta));
};
const norm = (name: string) => name.toLowerCase().replace(/[^a-z0-9]/g, "");
/** `fitbit` must match a row written as `Fitbit` or `google_health_fitbit`. */
const sameDevice = (a: string, b: string) => {
  const x = norm(a), y = norm(b);
  return !!x && !!y && (x.includes(y) || y.includes(x));
};
const pretty = (name: string) => name.replace(/_/g, " ");

type DailyRow = { value: number; day: string; source: string; today: boolean };

/** Index the connected device's own daily rows by metric.
 *
 * `?days=2` returns exactly [yesterday, today] as the *backend* reckons the
 * day, so the newest row in the response can lag the browser's date (nothing
 * filed yet today) but only leads it when the tick clock is simulated ahead —
 * hence today = the later of the two. Rows from any other device are dropped:
 * a WHOOP recovery score is not evidence that the Fitbit is working.
 *
 * Per metric, today's row wins and yesterday's is the fallback, which is what
 * the sleep block needs: Fitbit files last night's sleep under yesterday.
 */
function liveDaily(rows: SeededMetricRow[], devices: string[]) {
  const newest = rows.reduce((latest, r) => (r.day > latest ? r.day : latest), "");
  const browserToday = isoDay(new Date());
  const today = newest > browserToday ? newest : browserToday;
  const yesterday = shiftDay(today, -1);
  const index = new Map<string, DailyRow>();
  for (const row of rows) {
    if (typeof row.value !== "number" || !Number.isFinite(row.value)) continue;
    if (row.day !== today && row.day !== yesterday) continue;
    if (!devices.some((device) => sameDevice(row.source ?? "", device))) continue;
    const seen = index.get(row.metric);
    if (seen && seen.day >= row.day) continue;
    index.set(row.metric, { value: row.value, day: row.day, source: row.source ?? "", today: row.day === today });
  }
  return index;
}

type Resolved = {
  label: string;
  text: string;
  pill: string;
  live: boolean;
  muted: boolean;
  points: [number, number][];
  sub: string;
};

function resolve(tile: Tile, series: BiometricSeries | undefined, connected: boolean,
                 device: string, daily: Map<string, DailyRow>): Resolved {
  const points = series?.points ?? [];
  const last = points.at(-1)?.[0] ?? 0;
  const shown = points.filter(([t]) => t >= last - WINDOW_S);
  const windowValue = tile.sum
    ? shown.reduce((total, [, v]) => total + v, 0)
    : shown.at(-1)?.[1];

  // 1 — a real sample inside the strip window wins outright.
  if (series?.origin === "live" && shown.length) {
    return { label: tile.label, text: tile.format(windowValue as number), pill: "live",
             live: true, muted: false, points: shown, sub: pretty(series.source || device) };
  }
  const row = tile.daily ? daily.get(tile.daily) : undefined;
  // 2 — nothing intraday, but the connected device filed a daily roll-up.
  if (connected && row) {
    const format = tile.dailyFormat ?? tile.format;
    const when = tile.overnight ? "last night" : row.today ? "today" : "yesterday";
    return { label: tile.dailyLabel ?? tile.label, text: format(row.value), pill: `live · ${when}`,
             live: true, muted: false, points: [], sub: pretty(row.source || device) };
  }
  // 3 — connected, and this simply is not one of the things it measures.
  if (connected) {
    return { label: tile.label, text: "—", pill: `not on ${pretty(device)}`,
             live: false, muted: true, points: [], sub: "not measured" };
  }
  // 4 — nothing connected: the seeded demo day, labelled as such.
  const live = series?.origin === "live";
  return { label: tile.label, text: shown.length ? tile.format(windowValue as number) : "—",
           pill: live ? "live" : "seeded", live, muted: false, points: shown,
           sub: pretty(series?.source || "no device") };
}

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

function OriginPill({ text, live, muted }: { text: string; live: boolean; muted: boolean }) {
  const tone = live ? "bg-emerald-500/20 text-emerald-300"
    : muted ? "bg-zinc-800/70 text-zinc-500" : "bg-zinc-700/60 text-zinc-400";
  return (
    <span className={`shrink-0 rounded px-1 py-px text-[9px] font-semibold uppercase tracking-wider ${tone}`}>
      {text}
    </span>
  );
}

function MetricTile({ resolved, detail }: { resolved: Resolved; detail?: string }) {
  return (
    <div className={`min-w-[118px] flex-1 rounded-md border px-2.5 py-2 ${
      resolved.muted ? "border-zinc-800/60 bg-zinc-900/20 opacity-60" : "border-zinc-800 bg-zinc-900/40"}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-[10px] uppercase tracking-wider text-zinc-500">{resolved.label}</span>
        <OriginPill text={resolved.pill} live={resolved.live} muted={resolved.muted} />
      </div>
      <div className="mt-0.5 flex items-end justify-between gap-2">
        <span className={`text-sm font-semibold ${resolved.muted ? "text-zinc-600" : "text-white"}`}>
          {resolved.text}
        </span>
        <Spark points={resolved.points} />
      </div>
      <div className="muted truncate text-[10px]">{detail ? `${detail} · ${resolved.sub}` : resolved.sub}</div>
    </div>
  );
}

export function BiometricsStrip({ restingFallback = 58 }: { restingFallback?: number }) {
  const biometrics = usePoll(() => api.biometrics("heart_rate"), 5000);
  const others = usePoll(() => api.biometricsMulti(TILE_METRICS), 5000);
  const wearables = usePoll(() => api.wearablesStatus(), 15000);
  // Long format, two days: the strip needs each row's `source` to decide
  // whether a daily number is the connected device's or the demo day's.
  const seeded = usePoll(() => api.seededRows(2), 30000);

  const connected = wearables.data?.live_connected ?? false;
  const devices = wearables.data?.live_devices ?? [];
  const device = devices[0] ?? "wearable";
  const rows = seeded.data ?? [];
  const daily = liveDaily(rows, devices);

  const seededResting = rows.filter((r) => r.metric === "resting_hr")
    .sort((a, b) => a.day.localeCompare(b.day)).at(-1)?.value;
  const resting = daily.get("resting_hr")?.value ?? seededResting ?? restingFallback;
  const threshold = resting * RATIO;

  const points = biometrics.data?.points ?? [];
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

  const connection = connected
    ? `live: ${devices.join(", ") || "wearable"}`
    : "seeded";

  // deep / REM minutes ride along under the sleep tile's own number.
  const sleepDetail = [
    daily.get("deep_min") && `deep ${Math.round(daily.get("deep_min")!.value)}`,
    daily.get("rem_min") && `rem ${Math.round(daily.get("rem_min")!.value)}`,
  ].filter(Boolean).join(" · ");

  const tiles = TILES.filter((tile) => !tile.dailyOnly || (connected && tile.daily && daily.has(tile.daily)));

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
        {tiles.map((tile) => (
          <MetricTile
            key={tile.metric}
            resolved={resolve(tile, others.data?.series?.[tile.metric], connected, device, daily)}
            detail={tile.daily === "sleep_hours" && sleepDetail ? `${sleepDetail} min` : undefined}
          />
        ))}
      </div>
    </section>
  );
}
