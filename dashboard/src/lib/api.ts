import { mockDecisions, mockEpisodes, mockPending, mockScores, mockSeeded, mockStatus, mockSummary, mockTicks } from "./mock";
import type { Decision, Episode, Insight, PendingCheck, Scores, SeededDay, Status, Tick, TodaySummary } from "./types";

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

export const api = {
  status: ()=>request<Status>("/api/status",{...mockStatus,last_tick_t:Date.now()/1000}),
  ticks: async (n=60)=>{const r=await request<Tick[]|{ticks:Tick[]}>(`/api/ticks/recent?n=${n}`,mockTicks);return {...r,data:list(r.data,["ticks"])};},
  episodes: async (day?:string)=>{const r=await request<Episode[]|{episodes:Episode[]}>(`/api/episodes${day?`?day=${encodeURIComponent(day)}`:""}`,mockEpisodes);return {...r,data:list(r.data,["episodes"])};},
  decisions: async (limit=50)=>{const r=await request<Decision[]|{decisions:Decision[]}>(`/api/decisions?limit=${limit}`,mockDecisions);return {...r,data:list(r.data,["decisions"])};},
  insights: async (limit=50)=>{const r=await request<Insight[]|{insights:Insight[]}>(`/api/insights?limit=${limit}`,[]);return {...r,data:list(r.data,["insights"])};},
  scores: async (period:"daily"|"weekly"="daily")=>request<Scores>(`/api/scores?period=${period}`,mockScores),
  pendingChecks: async ()=>{const r=await request<PendingCheck[]|{pending_checks:PendingCheck[]}>("/api/pending_checks",mockPending);return {...r,data:list(r.data,["pending_checks"])};},
  summaryToday: async ()=>{const r=await request<TodaySummary|string[]|{summary:string[]}>("/api/summary/today",mockSummary);const data=Array.isArray(r.data)?{lines:r.data}:"lines" in r.data?r.data:{lines:r.data.summary};return {...r,data};},
  seeded: async (days=7)=>{const r=await request<SeededDay[]|{rows:SeededDay[]}|{seeded:SeededDay[]}>(`/api/seeded?days=${days}`,mockSeeded);return {...r,data:list(r.data,["rows","seeded"])};},
};
export type ApiResult<T>={data:T;mock:boolean};
