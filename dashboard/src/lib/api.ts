import { mockDecisions, mockEpisodes, mockPending, mockScores, mockSeeded, mockStatus, mockSummary, mockTicks } from "./mock";
import type { Decision, Episode, Insight, MetricScore, PendingCheck, Scores, SeededDay, Status, Tick, TodaySummary } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
export const configuredMock = process.env.NEXT_PUBLIC_MOCK === "1";
export class ApiOfflineError extends Error { constructor() { super("API offline"); this.name="ApiOfflineError"; } }

async function request<T>(path: string, mock: T): Promise<{data:T; mock:boolean}> {
  if (configuredMock) return {data:mock,mock:true};
  try {
    const response=await fetch(`${API_BASE}${path}`,{cache:"no-store",signal:AbortSignal.timeout(2500)});
    if (!response.ok) throw new Error(String(response.status));
    return {data:await response.json() as T,mock:false};
  } catch { return {data:mock,mock:true}; }
}
const list = <T>(value: T[] | Record<string,T[]>, keys: string[]): T[] => Array.isArray(value)?value:(keys.map(k=>value[k]).find(Boolean)??[]);

const hhmm = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const hhmmFromHours = (h: number) => { const hh = Math.floor(h % 24), mm = Math.round((h % 1) * 60); return `${String(hh).padStart(2,"0")}:${String(mm).padStart(2,"0")}`; };

export const api = {
  status: ()=>request<Status>("/api/status",{...mockStatus,last_tick_t:Date.now()/1000}),
  ticks: async (n=60)=>{const r=await request<Tick[]|{ticks:Tick[]}>(`/api/ticks/recent?n=${n}`,mockTicks);return {...r,data:list(r.data,["ticks"])};},
  episodes: async (day?:string)=>{const r=await request<Episode[]|{episodes:Episode[]}>(`/api/episodes${day?`?day=${encodeURIComponent(day)}`:""}`,mockEpisodes);return {...r,data:list(r.data,["episodes"])};},
  decisions: async (limit=50)=>{const r=await request<Decision[]|{decisions:Decision[]}>(`/api/decisions?limit=${limit}`,mockDecisions);return {...r,data:list(r.data,["decisions"])};},
  insights: async (limit=50)=>{const r=await request<Insight[]|{insights:Insight[]}>(`/api/insights?limit=${limit}`,[]);return {...r,data:list(r.data,["insights"])};},
  scores: async (period:"daily"|"weekly"="daily")=>{
    // Backend: {period, overall, scores:[{metric, layer, value, target, score, source, grade, note}]}
    type Row = { metric: string; layer: string; value: number | null; target: string; score: number; source: "live"|"seeded"; grade: "A"|"B"|"C"; note?: string | null };
    const r=await request<Scores|{overall:number|null; scores:Row[]}>(`/api/scores?period=${period}`,mockScores);
    if ("metrics" in r.data) return {...r,data:r.data};
    const metrics: MetricScore[] = (r.data.scores ?? []).map((row) => ({
      id: row.metric, layer: row.layer,
      metric: row.metric.replace(/_(daily|weekly)$/, "").replace(/_/g, " "),
      source: row.source, grade: row.grade, target: row.target,
      value: row.value === null || row.value === undefined ? "—" : Number.isInteger(row.value) ? row.value : Number(row.value.toFixed(2)),
      score: row.score,
    }));
    return {...r,data:{overall: r.data.overall ?? 0, metrics}};
  },
  pendingChecks: async ()=>{const r=await request<PendingCheck[]|{pending_checks:PendingCheck[]}>("/api/pending_checks",mockPending);return {...r,data:list(r.data,["pending_checks"])};},
  summaryToday: async ()=>{
    // Backend returns {day, lines:[{t, line, decision_id}]}; UI wants strings.
    type Raw = { day?: string; lines?: Array<string | { t?: number; line: string }> } | string[] | { summary: string[] };
    const r=await request<Raw>("/api/summary/today",mockSummary);
    const raw = Array.isArray(r.data) ? r.data : "summary" in r.data ? r.data.summary : (r.data.lines ?? []);
    const lines = raw.map((l) => typeof l === "string" ? l : (l.t ? `${hhmm(l.t)} — ${l.line}` : l.line));
    return {...r,data:{lines}};
  },
  seeded: async (days=7)=>{
    // Backend returns long-format rows {day, metric, value, unit, source}; pivot to one row per day.
    type Row = { day: string; metric: string; value: number };
    const r=await request<SeededDay[]|{rows:Row[]}|{seeded:SeededDay[]}>(`/api/seeded?days=${days}`,mockSeeded);
    const d = r.data as unknown;
    if (Array.isArray(d) || (d && typeof d === "object" && "seeded" in d)) return {...r,data:list(r.data as SeededDay[]|{seeded:SeededDay[]},["seeded"])};
    const rows = ((d as {rows?: Row[]}).rows ?? []);
    const byDay = new Map<string, Record<string, number>>();
    for (const row of rows) { const m = byDay.get(row.day) ?? {}; m[row.metric] = row.value; byDay.set(row.day, m); }
    const data: SeededDay[] = [...byDay.entries()].sort(([a],[b])=>a.localeCompare(b)).map(([day, m]) => ({
      date: day.slice(5).replace("-", "/"),
      sleep_h: m.sleep_hours ?? 0, hrv_ratio: m.hrv_rmssd_ratio ?? 0, steps: m.steps ?? 0, sri: m.sleep_regularity_sri ?? 0,
      caffeine_last: m.bed_time !== undefined ? `bed ${hhmmFromHours(m.bed_time)}` : undefined,
    }));
    return {...r,data};
  },
};
export type ApiResult<T>={data:T;mock:boolean};
