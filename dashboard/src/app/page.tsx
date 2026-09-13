import { BrianLive } from "@/components/brian/BrianLive";
import { PipelineDrawer } from "@/components/brian/PipelineDrawer";
import { BackendOffline } from "@/lib/score/backend";
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

async function load(goal: Goal): Promise<Loaded> {
  try {
    return { ok: true, data: await loadDashboardData({ goal }) };
  } catch (error) {
    return { ok: false, error };
  }
}

/**
 * Honest failure state: every number on this page comes from an engine run over
 * the pipeline's own data, so when either is missing there is nothing to show
 * and nothing is estimated in its place.
 */
function EngineDown({ error }: { error: unknown }) {
  const offline = error instanceof BackendOffline;
  const message = error instanceof Error ? error.message : String(error);
  const stderr = error instanceof EngineError ? error.stderr.trim() : "";
  return (
    <main className="mx-auto w-full max-w-6xl px-6 py-10">
      <section className="rounded-panel bg-surface p-6 md:p-8" aria-labelledby="engine-down-title">
        <h1 id="engine-down-title" className="text-2xl font-bold text-ink">
          {offline ? "The backend is unreachable" : "The scoring engine did not respond"}
        </h1>
        <p className="mt-2 text-muted">
          {offline
            ? "Bryan reads every number from the pipeline backend — glasses episodes and connected wearables. With the backend down there is no measurement behind a score, so no numbers can be shown. Start the backend and reload."
            : "Nothing on this page is estimated without an engine run, so there is no score to show."}{" "}
          The pipeline drawer below still reflects the live capture feed.
        </p>
        <pre className="mt-4 overflow-x-auto rounded-pin bg-bg p-4 text-sm whitespace-pre-wrap text-text">
          {stderr ? `${message}\n\n${stderr}` : message}
        </pre>
      </section>
    </main>
  );
}

export default async function Page({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const sp = await searchParams;
  const loaded = await load(parseGoal(sp.goal));

  return (
    <div className="brian min-h-dvh bg-bg text-text">
      {loaded.ok ? <BrianLive initial={loaded.data} /> : <EngineDown error={loaded.error} />}
      <PipelineDrawer />
    </div>
  );
}
