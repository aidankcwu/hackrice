import type { Status } from "@/lib/types";
const Stat=({label,value,alert=false}:{label:string;value:string|number;alert?:boolean})=><div className="stat"><span>{label}</span><strong className={alert?"text-rose-400":""}>{value}</strong></div>;
export function StatusBar({status}:{status?:Status}) { if(!status)return <div className="panel h-20 animate-pulse"/>; const age=Math.max(0,Date.now()/1000-status.last_tick_t);return <header className="panel flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
  <div><p className="eyebrow">LifeOS / live</p><h1 className="text-lg font-bold tracking-tight">Healthspan copilot</h1></div>
  <span className="badge border-amber-500/50 bg-amber-500/15 text-amber-300">{status.demo_mode?"DEMO_MODE":"PRODUCTION"}</span>
  <div className="ml-auto flex flex-wrap gap-5"><Stat label="SOURCE" value={status.source}/><Stat label="TICKS" value={status.tick_count.toLocaleString()}/><Stat label="AI COVERAGE" value={`${Math.round(status.ai_coverage*100)}%`}/><Stat label="T1" value={status.t1_busy?"BUSY":"READY"} alert={status.t1_busy}/><Stat label="DROPPED" value={status.dropped_escalations} alert={status.dropped_escalations>0}/><Stat label="LAST TICK" value={`${age.toFixed(1)}s ago`} alert={age>3}/></div>
 </header> }
