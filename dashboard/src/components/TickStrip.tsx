"use client";
import { api } from "@/lib/api";
import type { Scene, Tick } from "@/lib/types";
import { usePoll } from "@/lib/usePoll";
// `scene` is a long menu and gets longer, so the strip colours by *family* rather
// than per value: a new dorm room reads as home, a new plaza reads as outdoors, and
// nothing has to be recoloured when the enum grows. The four scenes that are their
// own metric (gym, sauna, cold plunge, restaurant) keep their own colour on top.
// Anything unmapped -- including a value this build predates -- gets FALLBACK, so an
// enum change upstream degrades to a neutral cell instead of a blank one.
const FALLBACK="bg-sky-600";
const SCENE_FAMILIES:readonly(readonly[readonly Scene[],string])[]=[
  [["home"],"bg-violet-500"],
  [["park","trail","campus","street","parking_lot","beach","nature","sports_venue","construction_site","outdoor_other"],"bg-emerald-500"],
  [["office","classroom","library","lab","cafe","bar","grocery_store","store","hospital","hotel","indoor_other"],"bg-indigo-500"],
  [["car","public_transit","airport"],"bg-slate-500"],
  [["restaurant"],"bg-orange-500"],
  [["gym"],"bg-fuchsia-500"],
  [["sauna"],"bg-red-500"],
  [["cold_plunge"],"bg-cyan-500"],
];
const colors:Partial<Record<Scene,string>>={};
for(const [scenes,color] of SCENE_FAMILIES) for(const scene of scenes) colors[scene]=color;
const sceneColor=(scene:string|undefined)=>colors[scene as Scene]??FALLBACK;
// The strip is one cell per tick, so its span in seconds depends on the capture
// cadence -- 1.5 s off Person A's glasses, not the 1 Hz SPEC §2.1 describes.
// Polled slowly: the cadence is configuration, it does not change mid-run.
export function TickStrip({ticks=[]}:{ticks?:Tick[]}) {const status=usePoll(api.status,30000);const interval=status.data?.tick_interval_s??1;const recent=ticks.slice(-30),current=recent.at(-1);return <section className="panel p-4"><div className="section-title"><span>Live ticks</span><span className="muted">{(1/interval).toFixed(2)} Hz · last {Math.round(recent.length*interval)}s</span></div><div className="flex gap-1">{recent.map(t=>{const a=t.ai;const marks=a?[a.food_present&&"F",a.screen_present&&"S",a.people_present&&"P",a.caffeine_visible&&"C",a.alcohol_visible&&"A"].filter(Boolean).join(""):"";return <div title={`${t.tick_id}: ${a?.scene??"AI unavailable"}${a?.caption?` — ${a.caption}`:""}`} key={t.tick_id} className={`flex h-9 min-w-0 flex-1 items-center justify-center rounded-sm text-[9px] font-black ${a?sceneColor(a.scene):"bg-zinc-700 text-zinc-400"}`}>{a?marks||"·":"×"}</div>})}</div>{current&&<><div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-zinc-300"><b className="text-white">{current.tick_id}</b><span>scene <em>{current.ai?.scene??"—"}</em></span><span>activity <em>{current.ai?.activity??"—"}</em></span><span>lux proxy <em>{current.sensor.lux_proxy}</em></span><span>flow <em>{current.sensor.flow_mag.toFixed(2)}</em></span><span>AI age <em>{current.ai?`${current.ai.age_ms} ms`:"missing"}</em></span></div>{current.ai?.caption&&<div className="mt-2 text-xs text-zinc-300"><em>{current.ai.caption}</em><div className="mt-1 flex flex-wrap gap-1">{current.ai.objects?.map(object=><span key={object} className="rounded-full bg-zinc-700 px-2 py-0.5 text-[10px] text-zinc-200">{object}</span>)}</div></div>}</>}</section>}
