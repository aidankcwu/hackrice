import Link from "next/link";
import { BrianHeader } from "@/components/brian/Header";
import { EngineError } from "@/lib/score/engine";
import { loadDashboardData } from "@/lib/score/loader";
import {
  GOALS,
  LAYER_ORDER,
  type DashboardData,
  type EngineFactor,
  type EngineInsight,
  type Goal,
  type LayerName,
  type LayerRow,
} from "@/lib/score/types";
import { FACTOR_UNITS, GRADE_LABELS, GRADE_SHRINK, LAYER_DISCOUNT } from "@/lib/score/units";
import { fmtH, tone } from "@/lib/tokens";

/**
 * Judge-facing evidence page: every factor the engine scored today with its
 * dose, hazard ratio, hours, evidence grade and the published source, plus
 * the leading indicators, the engine's own sentences and the raw observations.
 */

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

const isGoal = (value: unknown): value is Goal => GOALS.some((g) => g.value === value);

function parseGoal(raw: string | string[] | undefined): Goal {
  const value = Array.isArray(raw) ? raw[0] : raw;
  return isGoal(value) ? value : "average";
}

/** Keep `?goal=` and `?mock=1` when moving between the two pages. */
function withParams(path: string, goal: Goal, mock: boolean): string {
  const q = new URLSearchParams();
  if (goal !== "average") q.set("goal", goal);
  if (mock) q.set("mock", "1");
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}

// Fixed locale: the page is server-rendered, so the number format must not depend on the host.
const numberFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const fmtNum = (n: number): string => numberFormat.format(n);

const INSIGHT_KIND_LABELS: Record<EngineInsight["kind"], string> = {
  tonight: "Tonight",
  today: "Today",
  week: "This week",
  lever: "Lever",
  you: "Your data",
};

/** Units for the observation keys that are leading indicators rather than factors (`LEADING` in the engine). */
const LEADING_UNITS: Record<string, string> = {
  last_caffeine_hh: "h, decimal clock",
  night_screen_min: "min after 22:00",
  planned_bed_shift_min: "min vs habit",
};

const METHOD_NOTE =
  "Hours are a day's share of the life-expectancy change implied by published hazard ratios: hazards are summed in log space, converted with the Gompertz shift (adult mortality doubles about every 8 years) and spread over the days that remain — the microlife framing of Spiegelhalter & Blastland (BMJ 2012). Observational estimates are shrunk by grade and capped; leading indicators forecast tonight and are never scored as hazards. No biological age is claimed.";

function groupByLayer(factors: EngineFactor[]): Map<LayerName, EngineFactor[]> {
  const groups = new Map<LayerName, EngineFactor[]>();
  for (const layer of LAYER_ORDER) groups.set(layer, []);
  for (const f of factors) groups.get(f.layer)?.push(f);
  return groups;
}

function EngineDown({ error, goal, mock }: { error: unknown; goal: Goal; mock: boolean }) {
  const message = error instanceof Error ? error.message : String(error);
  const stderr = error instanceof EngineError ? error.stderr.trim() : "";
  return (
    <div className="brian min-h-dvh bg-bg text-text">
      <main className="mx-auto w-full max-w-6xl px-6 py-10">
        <section className="rounded-panel bg-surface p-6 md:p-8" aria-labelledby="engine-down-title">
          <h1 id="engine-down-title" className="text-2xl font-bold text-ink">
            The scoring engine did not respond
          </h1>
          <p className="mt-2 text-muted">
            This page lists the factors behind today&apos;s score; without an engine run there is nothing to list.
          </p>
          <pre className="mt-4 overflow-x-auto rounded-pin bg-bg p-4 text-sm whitespace-pre-wrap text-text">
            {stderr ? `${message}\n\n${stderr}` : message}
          </pre>
          <p className="mt-5 flex flex-wrap gap-3">
            <Link
              href="/factors?mock=1"
              className="tile inline-flex min-h-11 items-center rounded-full bg-bg px-4 text-sm font-medium text-ink"
            >
              Open with the fixture day instead (?mock=1)
            </Link>
            <Link
              href={withParams("/", goal, mock)}
              className="tile inline-flex min-h-11 items-center rounded-full bg-bg px-4 text-sm font-medium text-ink"
            >
              Back to today
            </Link>
          </p>
        </section>
      </main>
    </div>
  );
}

function DoseCell({ f }: { f: EngineFactor }) {
  if (!f.measured || f.dose === null) {
    return <span className="text-muted">not measured — imputed at the population reference</span>;
  }
  const unit = FACTOR_UNITS[f.key];
  return (
    <span className="tnum">
      {fmtNum(f.dose)}
      {unit ? ` ${unit}` : ""}
    </span>
  );
}

function LayerPanel({ name, row, factors }: { name: LayerName; row: LayerRow | undefined; factors: EngineFactor[] }) {
  const headingId = `layer-${name.replace(/[^a-z]+/gi, "-").toLowerCase()}`;
  return (
    <section className="rounded-panel bg-surface p-6 md:p-8" aria-labelledby={headingId}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h2 id={headingId} className="text-xl font-bold text-ink">
          {name}
        </h2>
        {row ? (
          <p className="tnum text-sm text-muted">
            Score <span className="font-medium text-ink">{Math.round(row.score)}</span> / 100 ·{" "}
            <span className="font-medium" style={{ color: tone(row.hours) }}>
              {fmtH(row.hours)}
            </span>{" "}
            today · {row.measured} of {row.total} factors measured
          </p>
        ) : (
          <p className="text-sm text-muted">No layer score returned.</p>
        )}
      </div>
      {row?.note ? <p className="mt-1 text-sm text-muted">{row.note}</p> : null}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-sm">
          <caption className="sr-only">
            Factors in the {name} layer: dose, hazard ratio, healthy-life hours today, evidence grade and source.
          </caption>
          <thead>
            <tr className="text-left text-muted">
              <th scope="col" className="py-2 pr-4 font-medium">
                Factor
              </th>
              <th scope="col" className="py-2 pr-4 font-medium">
                Dose
              </th>
              <th scope="col" className="py-2 pr-4 font-medium">
                HR
              </th>
              <th scope="col" className="py-2 pr-4 font-medium">
                Hours today
              </th>
              <th scope="col" className="py-2 pr-4 font-medium">
                Grade
              </th>
              <th scope="col" className="py-2 font-medium">
                Source
              </th>
            </tr>
          </thead>
          <tbody>
            {factors.map((f) => (
              <tr key={f.key} className={`border-t border-line align-top ${f.measured ? "" : "opacity-60"}`}>
                <th scope="row" className="py-3 pr-4 text-left font-medium whitespace-nowrap text-ink">
                  {f.label}
                </th>
                <td className="py-3 pr-4">
                  <DoseCell f={f} />
                </td>
                <td className="tnum py-3 pr-4 whitespace-nowrap">{f.hr === null ? "—" : f.hr.toFixed(3)}</td>
                <td className="tnum py-3 pr-4 font-medium whitespace-nowrap" style={{ color: tone(f.hours) }}>
                  {fmtH(f.hours)}
                </td>
                <td className="py-3 pr-4 whitespace-nowrap">{GRADE_LABELS[f.grade]}</td>
                <td className="min-w-[280px] py-3 text-sm text-text">{f.source}</td>
              </tr>
            ))}
            {factors.length === 0 && (
              <tr className="border-t border-line">
                <td colSpan={6} className="py-3 text-muted">
                  The engine returned no factors for this layer.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ForecastSection({ data }: { data: DashboardData }) {
  const fc = data.forecast;
  const tonight = data.insights.find((i) => i.kind === "tonight");
  return (
    <section className="rounded-panel bg-surface p-6 md:p-8" aria-labelledby="forecast-title">
      <h2 id="forecast-title" className="text-xl font-bold text-ink">
        Tonight&apos;s forecast — leading indicators, not scored as hazards
      </h2>
      <p className="tnum mt-2 text-sm text-muted">
        Sleep ~{fc.sleep_hours.toFixed(1)} h · HRV {fc.hrv_change_pct >= 0 ? "+" : "−"}
        {Math.abs(fc.hrv_change_pct).toFixed(0)}% · SRI {fc.sri_change_pts >= 0 ? "+" : "−"}
        {Math.abs(fc.sri_change_pts).toFixed(0)} pts · clock delayed ~{fc.melatonin_delay_min.toFixed(0)} min · bedtime{" "}
        {fc.bedtime}
      </p>
      {fc.drivers.length > 0 ? (
        <ul className="mt-4 list-disc space-y-1 pl-5">
          {fc.drivers.map((d) => (
            <li key={d}>{d}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-4 text-muted">No leading indicator fired today.</p>
      )}
      {fc.fix ? <p className="mt-4">{fc.fix}</p> : null}
      {tonight ? <p className="mt-4 text-sm text-muted">Source: {tonight.source}</p> : null}
    </section>
  );
}

function InsightsSection({ insights }: { insights: EngineInsight[] }) {
  return (
    <section className="rounded-panel bg-surface p-6 md:p-8" aria-labelledby="insights-title">
      <h2 id="insights-title" className="text-xl font-bold text-ink">
        What the engine wrote
      </h2>
      {insights.length === 0 ? (
        <p className="mt-4 text-muted">The engine wrote nothing for today.</p>
      ) : (
        <ul className="mt-4 space-y-4">
          {insights.map((i, idx) => (
            <li key={`${i.kind}-${idx}`} className="rounded-pin bg-bg p-4">
              <span className="inline-flex rounded-full bg-surface px-3 py-1 text-sm font-medium text-ink">
                {INSIGHT_KIND_LABELS[i.kind]}
              </span>
              <p className="mt-2">{i.text}</p>
              <p className="mt-1 text-sm text-muted">Source: {i.source}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ObservationsSection({ data }: { data: DashboardData }) {
  const labels = new Map(data.factors.map((f) => [f.key, f.label] as const));
  const entries = Object.entries(data.observations);
  return (
    <section className="rounded-panel bg-surface p-6 md:p-8" aria-labelledby="observations-title">
      <h2 id="observations-title" className="text-xl font-bold text-ink">
        Observations fed to the engine
      </h2>
      <p className="mt-2 text-sm text-muted">
        Only what the pipeline actually measured today. Anything absent here was imputed at the population reference
        and earns nothing.
      </p>
      {entries.length === 0 ? (
        <p className="mt-4 text-muted">No observations reached the engine.</p>
      ) : (
        <dl className="mt-4 grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
          {entries.map(([key, value]) => {
            const label = labels.get(key);
            const unit = FACTOR_UNITS[key] ?? LEADING_UNITS[key];
            return (
              <div key={key} className="flex items-baseline justify-between gap-4 border-b border-line py-2">
                <dt className="min-w-0 text-sm">
                  <span className="text-ink">{key}</span>
                  {label ? <span className="text-muted"> · {label}</span> : null}
                </dt>
                <dd className="tnum shrink-0 font-medium text-ink">
                  {fmtNum(value)}
                  {unit ? <span className="font-normal text-muted"> {unit}</span> : null}
                </dd>
              </div>
            );
          })}
        </dl>
      )}
    </section>
  );
}

export default async function FactorsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const sp = await searchParams;
  const goal = parseGoal(sp.goal);
  const mock = sp.mock === "1";

  let data: DashboardData;
  try {
    data = await loadDashboardData({ goal, mock });
  } catch (error) {
    return <EngineDown error={error} goal={goal} mock={mock} />;
  }

  const groups = groupByLayer(data.factors);
  const layerRows = new Map(data.layers.map((l) => [l.name, l] as const));
  const shrink = `A cohort ${GRADE_SHRINK.A_cohort}, B ${GRADE_SHRINK.B}, C ${GRADE_SHRINK.C}`;

  return (
    <div className="brian min-h-dvh bg-bg text-text">
      <BrianHeader person={data.person} source={data.source} goal={goal} active="factors" />
      <main className="mx-auto w-full max-w-6xl px-6 py-8">
        <h1 className="text-3xl font-bold text-ink">Where the numbers come from</h1>
        <div className="mt-4 max-w-3xl space-y-3">
          <p>
            Hours are a day&apos;s share of the life-expectancy change implied by published hazard ratios: each factor&apos;s
            dose maps to a relative hazard, the hazards are summed in log space and converted with the Gompertz shift,
            and the resulting years are spread over the days that remain — the microlife framing.
          </p>
          <p>
            Observational hazard ratios are shrunk by evidence grade ({shrink}; randomised trials count in full) and
            capped at ±0.6 log-hazard. Correlated factors within a layer are discounted — the largest effect counts in
            full, the rest at {LAYER_DISCOUNT.slice(1).join(", ")} — so a layer&apos;s hours are less than the sum of its
            rows. Factors the pipeline did not measure today are imputed at the population reference and earn nothing.
          </p>
        </div>

        <div className="mt-8 space-y-6">
          {LAYER_ORDER.map((name) => (
            <LayerPanel key={name} name={name} row={layerRows.get(name)} factors={groups.get(name) ?? []} />
          ))}
          <ForecastSection data={data} />
          <InsightsSection insights={data.insights} />
          <ObservationsSection data={data} />
        </div>

        <footer className="mt-10 border-t border-line pt-6 text-sm text-muted">
          <p>{METHOD_NOTE}</p>
          <p className="tnum mt-3">
            Scored {data.source.day} for {data.person.profileLabel.toLowerCase()} profile · engine run {data.engine_ms} ms
            · source {data.source.mode}
          </p>
          <p className="mt-4">
            <Link
              href={withParams("/", goal, mock)}
              className="tile inline-flex min-h-11 items-center rounded-full bg-surface px-4 text-sm font-medium text-ink"
            >
              Back to today
            </Link>
          </p>
        </footer>
      </main>
    </div>
  );
}
