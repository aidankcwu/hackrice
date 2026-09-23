"use client";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { appFetch } from "@/lib/runtime";
import { T } from "@/lib/tokens";
import type { EngineForecast, EngineProfile, ForecastView } from "@/lib/score/types";
import { clockTime, recoverableDrivers, withUnit } from "@/lib/score/instruments";
import { fmtSigned } from "./format";
import { H2, Panel } from "./Panel";

/**
 * "Tonight, if nothing changes" (screens.md §1.5) — the three forecast tiles,
 * the Because sentence built from the engine's own drivers, the largest
 * recoverable one written as "Still fixable:", and three sliders that re-run
 * `forecast_tonight` over a changed evening.
 *
 * The sliders move only the engine's leading indicators, and `POST /api/forecast`
 * re-runs the engine itself, so every counterfactual number on screen is the
 * engine's — clamped by it (sleep 3.5–10 h, HRV −40…+10 %, SRI −30…0, clock
 * 0–120 min) — and never arithmetic done here.
 */

const DEBOUNCE_MS = 220;

/** Slider bounds come from screens.md §1.5; they sit inside what the engine accepts. */
interface SliderSpec {
  key: "night_screen_min" | "last_caffeine_hh" | "alcohol_drinks";
  label: string;
  min: number;
  max: number;
  step: number;
  /** Big step for PageUp/PageDown. */
  bigStep: number;
  format: (v: number) => string;
}

const SLIDERS: readonly SliderSpec[] = [
  {
    key: "night_screen_min",
    label: "Screens after 22:00",
    min: 0,
    max: 120,
    step: 5,
    bigStep: 20,
    format: (v) => withUnit(v, "min"),
  },
  {
    key: "last_caffeine_hh",
    label: "Last caffeine",
    min: 12,
    max: 22,
    step: 0.5,
    bigStep: 2,
    format: (v) => clockTime(v),
  },
  {
    key: "alcohol_drinks",
    label: "Drinks",
    min: 0,
    max: 5,
    step: 1,
    bigStep: 2,
    format: (v) => `${v}`,
  },
];

const clamp = (v: number, s: SliderSpec): number => Math.max(s.min, Math.min(s.max, v));

export type SliderValues = Record<SliderSpec["key"], number>;

/** Today's measured evening, clamped into each slider's range so the thumb starts on a real value. */
function initialValues(observations: Readonly<Record<string, number>>): SliderValues {
  const read = (key: string, fallback: number): number => {
    const v = observations[key];
    return typeof v === "number" && Number.isFinite(v) ? v : fallback;
  };
  return {
    // A day with no caffeine sighting has nothing to move; the slider then starts
    // at its own ceiling, which is the engine's "outside the cutoff" case.
    night_screen_min: clamp(read("night_screen_min", 0), SLIDERS[0]),
    last_caffeine_hh: clamp(read("last_caffeine_hh", SLIDERS[1].max), SLIDERS[1]),
    alcohol_drinks: clamp(read("alcohol_drinks", 0), SLIDERS[2]),
  };
}

export interface TonightProps {
  f: ForecastView;
  /** Today's observations — the sliders start from the evening actually measured. */
  observations: Readonly<Record<string, number>>;
  /** Passed through to the engine so the cutoff and the bedtime are the wearer's. */
  profile: EngineProfile;
  /** Habitual sleep the forecast starts from (the engine defaults to 7.5). */
  baselineSleepH?: number;
}

export function Tonight({ f, observations, profile, baselineSleepH }: TonightProps) {
  const base = initialValues(observations);
  const [values, setValues] = useState<SliderValues>(base);
  const [what, setWhat] = useState<EngineForecast | null>(null);
  const [error, setError] = useState<string | null>(null);
  const groupId = useId();
  // Only the newest request may land: a slider drag fires several and they can
  // finish out of order.
  const seq = useRef(0);

  const touched =
    values.night_screen_min !== base.night_screen_min ||
    values.last_caffeine_hh !== base.last_caffeine_hh ||
    values.alcohol_drinks !== base.alcohol_drinks;

  useEffect(() => {
    if (!touched) {
      setWhat(null);
      setError(null);
      return;
    }
    const mine = ++seq.current;
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const res = await appFetch("/api/forecast", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
            signal: AbortSignal.timeout(8000),
            body: JSON.stringify({
              profile,
              baseline_sleep_h: baselineSleepH,
              today_obs: {
                night_screen_min: values.night_screen_min,
                last_caffeine_hh: values.last_caffeine_hh,
                alcohol_drinks: values.alcohol_drinks,
                planned_bed_shift_min: observations.planned_bed_shift_min ?? 0,
              },
            }),
          });
          if (!res.ok) throw new Error(`/api/forecast ${res.status}`);
          const json = (await res.json()) as EngineForecast;
          if (mine === seq.current) {
            setWhat(json);
            setError(null);
          }
        } catch (e) {
          if (mine === seq.current) {
            setWhat(null);
            setError(e instanceof Error ? e.message : String(e));
          }
        }
      })();
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // `base` is derived from `observations`, which is in the list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [touched, values, profile, baselineSleepH, observations]);

  const shown = what ?? f;
  const tiles: ReadonlyArray<[string, string]> = [
    ["Sleep", withUnit(shown.sleep_hours, "h", 1)],
    ["HRV", fmtSigned(shown.hrv_change_pct, 0, "%")],
    ["Clock", fmtSigned(shown.melatonin_delay_min, 0, " min")],
  ];

  const fixable = recoverableDrivers(f.drivers, observations, profileBedtime(profile, f));

  const onSlide = useCallback((key: SliderSpec["key"], v: number) => {
    setValues((prev) => ({ ...prev, [key]: v }));
  }, []);

  return (
    <Panel labelledBy="tonight-title">
      <H2 id="tonight-title" sub={`Habitual bedtime ${f.bedtime}.`}>
        Tonight, if nothing changes
      </H2>

      <dl className="m-0 grid grid-cols-3 gap-3">
        {tiles.map(([k, v]) => (
          <div key={k} className="rounded-2xl p-3 sm:p-4" style={{ background: T.bg }}>
            <dt className="text-xs font-medium" style={{ color: T.muted }}>
              {k}
            </dt>
            <dd className="tnum m-0 mt-1 text-2xl font-bold" style={{ color: T.ink }}>
              {v}
            </dd>
          </div>
        ))}
      </dl>

      {f.drivers.length > 0 && (
        <p className="m-0 mt-4 text-sm" style={{ color: T.text }}>
          Because: {f.drivers.join("; ")}.
        </p>
      )}

      {fixable.length > 0 ? (
        <p className="m-0 mt-2 text-sm font-medium" style={{ color: T.ink }}>
          Still fixable: {fixable[0].sentence}
        </p>
      ) : (
        <p className="m-0 mt-2 text-sm font-medium" style={{ color: T.ink }}>
          {f.fix}
        </p>
      )}

      <fieldset className="m-0 mt-6 border-0 p-0" style={{ borderTop: `1px solid ${T.line}` }}>
        <legend className="sr-only">Change tonight and re-run the forecast</legend>
        <div className="mt-5 flex flex-col gap-4">
          {SLIDERS.map((s) => {
            const id = `${groupId}-${s.key}`;
            return (
              <div key={s.key}>
                <div className="flex items-baseline justify-between gap-3">
                  <label htmlFor={id} className="text-sm" style={{ color: T.muted }}>
                    {s.label}
                  </label>
                  <output
                    htmlFor={id}
                    className="tnum text-sm font-semibold"
                    style={{ color: T.ink }}
                    aria-live="polite"
                  >
                    {s.format(values[s.key])}
                  </output>
                </div>
                <input
                  id={id}
                  type="range"
                  min={s.min}
                  max={s.max}
                  step={s.step}
                  value={values[s.key]}
                  onChange={(e) => onSlide(s.key, Number(e.target.value))}
                  onKeyDown={(e) => {
                    // The browser gives arrows and Home/End; PageUp/PageDown it
                    // steps by a tenth of the range, which on a 0–5 slider is
                    // less than one step.
                    if (e.key !== "PageUp" && e.key !== "PageDown") return;
                    e.preventDefault();
                    const d = e.key === "PageUp" ? s.bigStep : -s.bigStep;
                    onSlide(s.key, clamp(values[s.key] + d, s));
                  }}
                  // 44 px thumbs on a 4 px track, both vendor prefixes. The
                  // track colour stays ink-on-grey; colour is for data only.
                  className="mt-2 h-11 w-full cursor-pointer appearance-none bg-transparent [&::-moz-range-thumb]:h-11 [&::-moz-range-thumb]:w-11 [&::-moz-range-thumb]:cursor-pointer [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:bg-[var(--ink)] [&::-moz-range-track]:h-1 [&::-moz-range-track]:rounded-full [&::-moz-range-track]:bg-[var(--surface-2)] [&::-webkit-slider-runnable-track]:h-1 [&::-webkit-slider-runnable-track]:rounded-full [&::-webkit-slider-runnable-track]:bg-[var(--surface-2)] [&::-webkit-slider-thumb]:-mt-[1.25rem] [&::-webkit-slider-thumb]:h-11 [&::-webkit-slider-thumb]:w-11 [&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-[var(--ink)]"
                  aria-valuetext={s.format(values[s.key])}
                />
              </div>
            );
          })}
        </div>
      </fieldset>

      <p className="m-0 mt-4 text-sm" style={{ color: error ? T.cost : T.muted }} aria-live="polite">
        {error
          ? "Tonight could not be re-run just now; the numbers above are the measured evening."
          : what
            ? `With ${sliderSummary(values, base)}: ${withUnit(what.sleep_hours, "h", 1)}, HRV ${fmtSigned(what.hrv_change_pct, 0, "%")}, clock ${fmtSigned(what.melatonin_delay_min, 0, " min")}.`
            : "Move a slider to see tonight re-scored."}
      </p>
    </Panel>
  );
}

/** Only the sliders the wearer actually moved, so the sentence reads as a change. */
function sliderSummary(values: SliderValues, base: SliderValues): string {
  const parts = SLIDERS.filter((s) => values[s.key] !== base[s.key]).map((s) => {
    if (s.key === "night_screen_min") {
      return values.night_screen_min === 0 ? "no screens after 22:00" : `${s.format(values[s.key])} of screens after 22:00`;
    }
    if (s.key === "last_caffeine_hh") return `last caffeine at ${s.format(values[s.key])}`;
    return values.alcohol_drinks === 0 ? "no drinks" : `${values.alcohol_drinks} drink${values.alcohol_drinks === 1 ? "" : "s"}`;
  });
  return parts.join(" and ");
}

/**
 * The bedtime the "Lights out at …" line quotes. `ForecastView.bedtime` is
 * already the measured habit as `HH:MM` (or a dash when no `bed_time` row
 * exists anywhere in the week), so the profile hour is read only as the decimal
 * the recoverable-driver helper wants, and never quoted when the habit is not
 * measured.
 */
function profileBedtime(profile: EngineProfile, f: ForecastView): number {
  if (f.bedtime === "—") return Number.NaN;
  const [h, m] = f.bedtime.split(":").map(Number);
  if (Number.isFinite(h) && Number.isFinite(m)) return h + m / 60;
  return profile.bedtime_hh ?? 23;
}
