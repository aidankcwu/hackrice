import Link from "next/link";
import { BrianLive } from "@/components/brian/BrianLive";
import { PipelineDrawer } from "@/components/brian/PipelineDrawer";
import { EngineError } from "@/lib/score/engine";
import { loadDashboardData } from "@/lib/score/loader";
import { GOALS, type DashboardData, type Goal } from "@/lib/score/types";

// Every request re-runs the engine on the pipeline's current day; nothing here is cacheable.
export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

const isGoal = (value: unknown): value is Goal => GOALS.some((g) => g.value === value);

function parseGoal(raw: string | string[] | undefined): Goal {
  const value = Array.isArray(raw) ? raw[0] : raw;
  return isGoal(value) ? value : "average";
}

type Loaded = { ok: true; data: DashboardData } | { ok: false; error: unknown };

async function load(goal: Goal, mock: boolean): Promise<Loaded> {
  try {
    return { ok: true, data: await loadDashboardData({ goal, mock }) };
  } catch (error) {
    return { ok: false, error };
  }
}

/** Honest failure state: no numbers are shown when there is no engine run behind them. */
function EngineDown({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  const stderr = error instanceof EngineError ? error.stderr.trim() : "";
  return (
    <main className="mx-auto w-full max-w-6xl px-6 py-10">
      <section className="rounded-panel bg-surface p-6 md:p-8" aria-labelledby="engine-down-title">
        <h1 id="engine-down-title" className="text-2xl font-bold text-ink">
          The scoring engine did not respond
        </h1>
        <p className="mt-2 text-muted">
          Nothing on this page is estimated without an engine run, so there is no score to show. The pipeline drawer
          below still reflects the live capture feed.
        </p>
        <pre className="mt-4 overflow-x-auto rounded-pin bg-bg p-4 text-sm whitespace-pre-wrap text-text">
          {stderr ? `${message}\n\n${stderr}` : message}
        </pre>
        <p className="mt-5">
          <Link
            href="/?mock=1"
            className="tile inline-flex min-h-11 items-center rounded-full bg-bg px-4 text-sm font-medium text-ink"
          >
            Open with the fixture day instead (?mock=1)
          </Link>
        </p>
      </section>
    </main>
  );
}

export default async function Page({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const sp = await searchParams;
  const goal = parseGoal(sp.goal);
  const mock = sp.mock === "1";
  const loaded = await load(goal, mock);

  return (
    <div className="brian min-h-dvh bg-bg text-text">
      {loaded.ok ? <BrianLive initial={loaded.data} /> : <EngineDown error={loaded.error} />}
      <PipelineDrawer />
    </div>
  );
}
