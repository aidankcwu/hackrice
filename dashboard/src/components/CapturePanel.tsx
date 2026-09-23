import { labelerStatus, parseCapture } from "@/lib/capture";
import type { Status } from "@/lib/types";

const shown = (value: string | number | undefined) => value ?? "—";
const Field = ({label, value}:{label:string; value:string|number|undefined}) => <div className="min-w-0"><dt className="text-[9px] font-bold uppercase tracking-[.12em] text-zinc-600">{label}</dt><dd className="truncate text-xs font-semibold text-zinc-200">{shown(value)}</dd></div>;

export function CapturePanel({status}:{status?:Status}) {
  if (!status) return <section className="panel mt-3 h-24 animate-pulse" aria-label="Capture"/>;
  if (status.source === "sim") return <section className="panel mt-3 px-4 py-3"><h2 className="section-title !mb-1">Capture</h2><p className="muted">simulated source — no glasses/VLM stats</p></section>;
  const data = parseCapture(status);
  const dot = data.phone.connected === undefined ? "bg-zinc-600" : data.phone.connected ? "bg-emerald-400" : "bg-rose-400";
  const yesNo = data.phone.connected === undefined ? undefined : data.phone.connected ? "yes" : "no";
  return <section className="panel mt-3 px-4 py-3">
    <div className="section-title"><h2>Capture</h2><span className="muted">source · {status.source}</span></div>
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-[1fr_1.15fr_1.6fr_.75fr]">
      <div><h3 className="mb-2 text-[10px] font-extrabold uppercase tracking-[.14em] text-zinc-400">phone</h3><dl className="grid grid-cols-2 gap-x-3 gap-y-2"><div><dt className="text-[9px] font-bold uppercase tracking-[.12em] text-zinc-600">connected</dt><dd className="flex items-center gap-1.5 text-xs font-semibold text-zinc-200"><span className={`h-1.5 w-1.5 rounded-full ${dot}`}/>{shown(yesNo)}</dd></div><Field label="packets" value={data.phone.packets}/><Field label="last packet age" value={data.phone.lastAgeS === undefined ? undefined : `${data.phone.lastAgeS.toFixed(1)} s`}/><Field label="transit" value={data.phone.transitMs === undefined ? undefined : `${data.phone.transitMs.toFixed(0)} ms`}/></dl></div>
      <div><h3 className="mb-2 text-[10px] font-extrabold uppercase tracking-[.14em] text-zinc-400">T0 loop</h3><dl className="grid grid-cols-2 gap-x-3 gap-y-2"><Field label="ticks emitted" value={data.loop.ticks}/><Field label="tick rate" value={data.loop.rateHz === undefined ? undefined : `${data.loop.rateHz.toFixed(2)} Hz`}/><Field label="sensor p50" value={data.loop.sensorP50Ms === undefined ? undefined : `${data.loop.sensorP50Ms.toFixed(1)} ms`}/><Field label="dropped / converted" value={`${shown(data.loop.dropped)} / ${shown(data.loop.converted)}`}/></dl></div>
      <div><h3 className="mb-2 text-[10px] font-extrabold uppercase tracking-[.14em] text-zinc-400">VLM</h3><dl className="grid grid-cols-2 gap-x-3 gap-y-2 lg:grid-cols-3"><Field label="model" value={data.vlm.model}/>{data.watcher ? <><Field label="labeler" value={data.labeler ? labelerStatus(data.labeler) : undefined}/><Field label="frames sent per hour" value={data.labeler?.framesSentPerHour}/></> : <Field label="coverage" value={data.vlm.coverage === undefined ? undefined : `${Math.round(data.vlm.coverage*100)}%`}/>}<Field label="latency p50 / p95" value={data.vlm.latencyP50Ms === undefined && data.vlm.latencyP95Ms === undefined ? undefined : `${shown(data.vlm.latencyP50Ms)} / ${shown(data.vlm.latencyP95Ms)} ms`}/><Field label="calls" value={data.vlm.calls}/><Field label="dropped (over 1.5 s budget)" value={data.vlm.overBudget}/><Field label="last error" value={data.vlm.lastError}/></dl></div>
      <div><h3 className="mb-2 text-[10px] font-extrabold uppercase tracking-[.14em] text-zinc-400">frames</h3><dl className="grid grid-cols-2 gap-x-3 gap-y-2 xl:grid-cols-1"><Field label="ring size" value={data.frames.size}/><Field label="bytes" value={data.frames.bytes === undefined ? undefined : data.frames.bytes.toLocaleString()}/></dl></div>
    </div>
  </section>;
}
