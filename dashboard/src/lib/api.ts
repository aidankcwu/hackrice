import { mockBiometrics, mockBiometricsMulti, mockDecisions, mockEpisodes, mockHealthspan, mockPending, mockQuestions, mockScores, mockSeeded, mockSeededRows, mockStatus, mockSummary, mockTicks, mockWearablesStatus } from "./mock";
import type { AnswerResult, AskResult, Biometrics, BiometricsMulti, Decision, Episode, Healthspan, Insight, MetricScore, PendingCheck, Question, Recap, Scores, SeededDay, SeededMetricRow, Session, Status, Tick, WearablesStatus } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8010";
export const configuredMock = process.env.NEXT_PUBLIC_MOCK === "1";
export class ApiOfflineError extends Error { constructor() { super("API offline"); this.name="ApiOfflineError"; } }

/** Reads fail loudly. The `mock` argument is served only when NEXT_PUBLIC_MOCK=1
 *  asks for it explicitly -- a backend that does not answer throws, so every
 *  caller renders its own "not measured" / "did not answer" state instead of a
 *  fabrication that looks like a measurement (product rule R1). */
async function request<T>(path: string, mock: T): Promise<{data:T; mock:boolean}> {
  if (configuredMock) return {data:mock,mock:true};
  const response=await fetch(`${API_BASE}${path}`,{cache:"no-store",signal:AbortSignal.timeout(2500)});
  if (!response.ok) throw new Error(`${path} -> ${response.status}`);
  return {data:await response.json() as T,mock:false};
}
const list = <T>(value: T[] | Record<string,T[]>, keys: string[]): T[] => Array.isArray(value)?value:(keys.map(k=>value[k]).find(Boolean)??[]);

/** Actions, unlike polls, must fail loudly: a judge session that silently did not
 *  start is worse than an error on screen. So no mock fallback and no timeout --
 *  `POST /api/recap` runs an LLM call and legitimately takes several seconds. */
async function action<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST", cache: "no-store",
    headers: {"content-type": "application/json"},
    body: JSON.stringify(body ?? {}),
  });
  if (!response.ok) throw new Error(`${path} -> ${response.status}`);
  return await response.json() as T;
}

const hhmm = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
// 1 -> a compact tag, in the order the persona cares about (SPEC §14.1).
const JOURNAL: Array<[string, string]> = [["journal_caffeine_late","caff"],["journal_alcohol","alc"],["journal_nicotine","nic"],["journal_cannabis","can"]];
const journalTags = (m: Record<string, number>) => { const tags = JOURNAL.filter(([k])=>m[k]===1).map(([,label])=>label); return tags.length?tags.join("\u00b7"):undefined; };
const hhmmFromHours = (h: number) => { const hh = Math.floor(h % 24), mm = Math.round((h % 1) * 60); return `${String(hh).padStart(2,"0")}:${String(mm).padStart(2,"0")}`; };

export const api = {
  status: ()=>request<Status>("/api/status",{...mockStatus,last_tick_t:Date.now()/1000}),
  ticks: async (n=60)=>{const r=await request<Tick[]|{ticks:Tick[]}>(`/api/ticks/recent?n=${n}`,mockTicks);return {...r,data:list(r.data,["ticks"])};},
  episodes: async (day?:string)=>{const r=await request<Episode[]|{episodes:Episode[]}>(`/api/episodes${day?`?day=${encodeURIComponent(day)}`:""}`,mockEpisodes);return {...r,data:list(r.data,["episodes"])};},
  decisions: async (limit=50)=>{const r=await request<Decision[]|{decisions:Decision[]}>(`/api/decisions?limit=${limit}`,mockDecisions);return {...r,data:list(r.data,["decisions"])};},
  // -- ask / answer (docs/API.md "Ask / answer"). The poll falls back to mock
  // data like every other read; the two POSTs are actions, so they fail loudly
  // -- an answer that silently went nowhere is the one failure a demo cannot see.
  questions: async (limit=20)=>{const r=await request<Question[]|{questions:Question[]}>(`/api/questions?limit=${limit}`,mockQuestions);return {...r,data:list(r.data,["questions"])};},
  /** Omit `questionId` to answer whichever question is open (404 when none is). */
  answer: (questionId: string | null | undefined, text: string) =>
    action<AnswerResult>("/api/answer", questionId ? {question_id: questionId, text} : {text}),
  /** Demo/debug ask. Still passes the §4 guards, so `question_id` may be null. */
  ask: (text: string) => action<AskResult>("/api/ask", {text}),
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
    const lines = raw.map((l) => typeof l === "string" ? l : (l.t && !/^\d{1,2}:\d{2}/.test(l.line) ? `${hhmm(l.t)} — ${l.line}` : l.line));
    return {...r,data:{lines}};
  },
  seeded: async (days=7)=>{
    // Backend returns long-format rows {day, metric, value, unit, source}; pivot to one row per day.
    type Row = { day: string; metric: string; value: number; source?: string };
    const r=await request<SeededDay[]|{rows:Row[]}|{seeded:SeededDay[]}>(`/api/seeded?days=${days}`,mockSeeded);
    const d = r.data as unknown;
    if (Array.isArray(d) || (d && typeof d === "object" && "seeded" in d)) return {...r,data:list(r.data as SeededDay[]|{seeded:SeededDay[]},["seeded"])};
    const rows = ((d as {rows?: Row[]}).rows ?? []);
    const byDay = new Map<string, {values: Record<string, number>; sources: Record<string, string>}>();
    for (const row of rows) {
      const day = byDay.get(row.day) ?? {values:{},sources:{}};
      day.values[row.metric] = row.value;
      if (row.source) day.sources[row.metric] = row.source;
      byDay.set(row.day, day);
    }
    const data: SeededDay[] = [...byDay.entries()].sort(([a],[b])=>a.localeCompare(b)).map(([day, {values:m, sources}]) => ({
      date: day.slice(5).replace("-", "/"),
      sleep_h: m.sleep_hours ?? 0, hrv_ratio: m.hrv_rmssd_ratio ?? 0, steps: m.steps ?? 0, sri: m.sleep_regularity_sri ?? 0,
      caffeine_last: m.bed_time !== undefined ? `bed ${hhmmFromHours(m.bed_time)}` : undefined,
      recovery: m.recovery_score, resting_hr: m.resting_hr, run_km: m.run_km,
      journal: journalTags(m), sources,
    }));
    return {...r,data};
  },
  // The same rows, unpivoted. `api.seeded` flattens a day into one `SeededDay`
  // and drops every metric the 7-day table does not print; the wearable strip
  // needs the long form, because it has to ask *which device* wrote each
  // metric before it may call the number live (docs/WEARABLES.md).
  seededRows: async (days=2)=>{
    const r=await request<SeededMetricRow[]|{rows:SeededMetricRow[]}>(`/api/seeded?days=${days}`,mockSeededRows);
    return {...r,data:list(r.data,["rows"])};
  },
  // SPEC §14.2: the intraday HR series, on the tick clock, for the HR strip.
  biometrics: async (metric="heart_rate", fromT?: number, toT?: number)=>{
    const query = new URLSearchParams({metric});
    if (fromT !== undefined) query.set("from", String(Math.floor(fromT)));
    if (toT !== undefined) query.set("to", String(Math.ceil(toT)));
    const r = await request<Biometrics>(`/api/biometrics?${query.toString()}`, mockBiometrics());
    return {...r, data: {metric: r.data?.metric ?? metric, source: r.data?.source ?? "", origin: r.data?.origin ?? "seed", points: r.data?.points ?? []}};
  },
  // Several series in one round trip; each entry says whether the numbers came
  // off a real device (`live`) or the seeded demo day (`seed`).
  biometricsMulti: async (metrics: string[], fromT?: number, toT?: number)=>{
    const query = new URLSearchParams({metrics: metrics.join(",")});
    if (fromT !== undefined) query.set("from", String(Math.floor(fromT)));
    if (toT !== undefined) query.set("to", String(Math.ceil(toT)));
    const r = await request<BiometricsMulti>(`/api/biometrics?${query.toString()}`, mockBiometricsMulti(metrics));
    const series = r.data?.series ?? {};
    return {...r, data: {series: Object.fromEntries(metrics.map(m => [m, {
      source: series[m]?.source ?? "", origin: series[m]?.origin ?? "seed", points: series[m]?.points ?? [],
    }]))} as BiometricsMulti};
  },
  wearablesStatus: async ()=>{
    const r = await request<WearablesStatus>("/api/wearables/status", mockWearablesStatus);
    return {...r, data: {
      metrics: r.data?.metrics ?? [], live_connected: r.data?.live_connected ?? false,
      live_devices: r.data?.live_devices ?? [], catalogue: r.data?.catalogue ?? {},
    } as WearablesStatus};
  },
  // -- judge session + recap. `sessionCurrent` polls; the rest are actions. --
  sessionCurrent: async (): Promise<Session|null> => {
    try {
      const response = await fetch(`${API_BASE}/api/session/current`,{cache:"no-store",signal:AbortSignal.timeout(2500)});
      if (!response.ok) return null;
      return await response.json() as Session|null;
    } catch { return null; }
  },
  sessionStart: (name: string) => action<Session>("/api/session/start", {name}),
  sessionEnd: () => action<Session>("/api/session/end"),
  /** Omit `sessionId` for the last 15 minutes of the tick clock. `speak` sends it
   *  out the glasses; the backend defaults it to true, so pass it explicitly. */
  recap: (sessionId?: string, speak = true) =>
    action<Recap>("/api/recap", sessionId ? {session_id: sessionId, speak} : {speak}),
  latestRecap: async (): Promise<Recap|null> => {
    try {
      const response = await fetch(`${API_BASE}/api/recap/latest`,{cache:"no-store",signal:AbortSignal.timeout(2500)});
      if (!response.ok) return null;   // 404 just means nobody has asked for one yet
      return await response.json() as Recap;
    } catch { return null; }
  },
  // Dose-response healthspan view for one day (default: the pipeline's today).
  // Array/object defaults only — never a mock-field spread under live data.
  healthspan: async (day?: string) => {
    const r = await request<Healthspan>(`/api/healthspan${day ? `?day=${encodeURIComponent(day)}` : ""}`, mockHealthspan);
    return {...r, data: {...r.data, layers: r.data?.layers ?? {}, factors: r.data?.factors ?? [], ledger: r.data?.ledger ?? [], levers: r.data?.levers ?? [], levers_free: r.data?.levers_free ?? [], insights: r.data?.insights ?? [], pins: r.data?.pins ?? [], effects: r.data?.effects ?? [], provenance: r.data?.provenance ?? {}, conventions: r.data?.conventions ?? []} as Healthspan};
  },
};
export type ApiResult<T>={data:T;mock:boolean};
