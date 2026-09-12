import type { Biometrics, BiometricsMulti, Decision, DecisionAction, Episode, PendingCheck, Scores, SeededDay, Status, Tick, TodaySummary, WearablesStatus } from "./types";
const now = Date.now() / 1000;
const scenes = ["office","office","restaurant","restaurant","street","park","park","gym"];
export const mockTicks: Tick[] = Array.from({length:30},(_,i) => { const scene=scenes[Math.floor(i/4)%scenes.length]; const missing=i%5===0; const caption=`person in ${scene}`; return {v:1,tick_id:`t_${1740+i}`,t:now-29+i,seq:1740+i,sensor:{lux_proxy:scene==="park"?510:180,cct:4100,hist_spread:.62,frame_delta:.08+(i%4)*.04,flow_mag:.04,sharpness:88,phash:`e3a91c04b7d2${i.toString().padStart(4,"0")}`},device:{accel_rms:.04,gps_speed:scene==="street"?1.2:.2},...(!missing&&{ai:{as_of:now-29+i-.3,age_ms:300,scene,activity:scene==="gym"?"exercising":"seated",food_present:scene==="restaurant",food_type:scene==="restaurant"?"mixed":"none",caffeine_visible:i===12,alcohol_visible:i===15,screen_present:scene==="office",vegetation_visible:scene==="park",people_present:["restaurant","park"].includes(scene),caption,objects:["person",scene==="office"?"laptop":"chair"],drink:i===12?"coffee":i===15?"alcohol":"none",conf:.86}}),frame_ref:`f_${1740+i}`}; });
// Every action type, both speak outcomes, a "nothing", and a dropped
// escalation, so mock mode exercises every state the decision feed renders.
type DecisionSeed = { trigger: string; text: string; conf: number; actions: DecisionAction[]; spoke: boolean; dropped?: boolean; drop_reason?: string };
const decisionSeed: DecisionSeed[] = [
 {trigger:"food_in_frame",text:"Mixed lunch with two colleagues, restaurant. Vegetables present, no visible dessert.",conf:.81,spoke:false,actions:[{type:"annotate",line:"12:31 lunch, mixed plate, with people"},{type:"log_insight",category:"diet",text:"Second mixed-plate meal today; vegetables in both."}]},
 {trigger:"caffeine_seen",text:"Coffee after your personal cutoff; this lands close enough to bedtime to cost sleep depth.",conf:.88,spoke:true,actions:[{type:"annotate",line:"16:42 coffee, after cutoff"},{type:"log_insight",category:"caffeine",text:"Cutoff missed on 3 of the last 5 days."},{type:"watch",after_s:900,reason:"check whether a second cup appears"},{type:"speak",text:"That is a late coffee — about six hours before your usual bedtime.",urgency:"normal"}]},
 {trigger:"screen_sustained",text:"Focused screen work has continued for 52 minutes with no break in frame.",conf:.76,spoke:false,actions:[{type:"annotate",line:"15:48 screen block, 52 min"},{type:"watch",after_s:600,reason:"check whether a screen break happened"}]},
 {trigger:"watch:screen_sustained",text:"The screen break never happened; the block is now 74 minutes long.",conf:.84,spoke:false,actions:[{type:"annotate",line:"15:58 screen block continues, 74 min"},{type:"speak",text:"You have been on screen for over an hour — worth standing up.",urgency:"low"}]},
 {trigger:"people_sustained",text:"Relaxed conversation with two people over coffee.",conf:.72,spoke:false,actions:[{type:"annotate",line:"10:47 conversation, two people"}]},
 {trigger:"alcohol_seen",text:"Alcohol detected close to bedtime; recovery will likely dip tomorrow morning.",conf:.79,spoke:true,actions:[{type:"annotate",line:"21:12 alcohol sighting"},{type:"log_insight",category:"alcohol",text:"Third sighting this week, all after 21:00."},{type:"speak",text:"Noted the drink — I will check your recovery in the morning.",urgency:"high"}]},
 {trigger:"outdoor_sustained",text:"Brief step outside, not long enough to count toward the nature dose.",conf:.55,spoke:false,actions:[{type:"nothing"}]},
 {trigger:"biometric_anomaly",text:"Heart rate rose to 96 bpm while seated and still for six minutes.",conf:.68,spoke:false,actions:[{type:"log_insight",category:"stress",text:"Seated HR spike with no matching motion."},{type:"watch",after_s:300,reason:"re-check heart rate once the spike settles"},{type:"speak",text:"Your heart rate is up while you are sitting still.",urgency:"low"}]},
 {trigger:"stillness",text:"Low frame motion sustained for five minutes; deep work or rest.",conf:.61,spoke:false,actions:[{type:"annotate",line:"14:20 stillness, 5 min"}]},
 {trigger:"gym_session",text:"Resistance training session underway.",conf:.83,spoke:false,actions:[{type:"annotate",line:"18:05 training session"},{type:"log_insight",category:"movement",text:"First resistance session of the week."}]},
 {trigger:"food_in_frame",text:"Escalation arrived while the reasoner was occupied.",conf:0,spoke:false,dropped:true,drop_reason:"t1_busy — dropped, never queued",actions:[]},
];
export const mockDecisions: Decision[] = decisionSeed.map((d,i)=>({id:`d_${String(decisionSeed.length-i).padStart(4,"0")}`,t:now-i*420,trigger:d.trigger,trigger_tick_id:`t_${1769-i}`,episode_id:d.dropped?null:`e_000${(i%6)+1}`,interpretation:d.text,confidence:d.conf,actions:d.actions,spoke:d.spoke,dropped:!!d.dropped,drop_reason:d.drop_reason??null,latency_ms:d.dropped?0:940+i*137,model:d.dropped?"\u2014":"gpt-5.4-mini"}));
export const mockEpisodes: Episode[] = [
 ["e1","screen_block",now-8*3600,now-6.8*3600,4320,false,"Morning focus"], ["e2","conversation",now-6.5*3600,now-6.1*3600,1440,false,"Coffee chat"], ["e3","meal",now-5.8*3600,now-5.2*3600,2160,false,"Lunch"], ["e4","outdoor_block",now-4.8*3600,now-4.35*3600,1620,false,"Park walk"], ["e5","gym_session",now-2.2*3600,now-1.4*3600,2880,false,"Training"], ["e6","screen_block",now-2400,null,2400,true,"Current work"]
 ].map(([id,kind,start_t,end_t,duration_s,open,label])=>({id,kind,start_t,end_t,duration_s,open,label} as Episode));
const metricRows: Array<[string,string,"live"|"seeded","A"|"B"|"C",string,string,number]> = [
 ["Light","Daytime light dose","seeded","A","≥30 min before 10","38 min",.86],["Light","Evening light","seeded","A","≤10 lux","8 lux",.92],
 ["Sleep","Duration","seeded","A","7–9 h","6.2 h",.48],["Sleep","Regularity (SRI)","seeded","A","≥80","76",.72],
 ["Movement","Steps","seeded","A","≥7,000","8,420",.88],["Movement","VILPA","seeded","A","3–4 min/day","3.4 min",.9],["Movement","Resistance training","live","A","2 sessions/week","1 session",.5],["Movement","Gait speed","seeded","A","≥1.2 m/s","1.24 m/s",.93],["Movement","Balance","seeded","B","10 sec","10 sec",1],
 ["Social","Integration","live","A","≥2 episodes/day","3 episodes",.9],["Nature","Weekly dose","live","B","≥120 min/week","94 min",.74],["Heat","Sauna","seeded","B","2–3×/week","2×",.82],["Cold","Cold plunge","seeded","C","log only","1×",.05],
 ["Stress","HRV recovery","seeded","B","≥ baseline","0.91×",.55],["Stress","Screen / work hours","live","A","<55 h/week","43 h",.79],["Stress","Breathwork","seeded","B","5 min/day","4 min",.75],
 ["Diet","Mediterranean pattern","live","A","mostly aligned","mixed",.7],["Diet","Caffeine cutoff","live","B","bedtime −9 h","late",.3],["Diet","Alcohol","live","A","none","1 sighting",.2],["Noise","Night noise","seeded","A","<45 dB","41 dB",.87],["Purpose","Life purpose","seeded","B","weekly check-in","complete",.9]
 ];
export const mockScores: Scores={overall:.71,metrics:metricRows.map((m,i)=>({id:`m${i}`,layer:m[0],metric:m[1],source:m[2],grade:m[3],target:m[4],value:m[5],score:m[6]}))};
const seededSources: Record<string,string> = {sleep_hours:"whoop",hrv_rmssd_ratio:"whoop",recovery_score:"whoop",resting_hr:"oura",run_km:"apple_watch",steps:"apple_watch",sleep_regularity_sri:"oura"};
const seededSeed: Array<[string,number,number,number,number,string,number,number,number,string]> = [
 ["Sep 06",7.8,1.08,9210,86,"13:10",78,54,10.2,""],["Sep 07",7.4,1.03,8020,84,"14:05",71,55,0,""],["Sep 08",6.1,.82,7140,72,"18:20",41,61,0,"caff·alc"],
 ["Sep 09",7.6,1.05,10340,85,"12:45",74,54,16.1,""],["Sep 10",5.9,.76,6320,68,"19:05",34,63,0,"caff"],["Sep 11",6.2,.81,8420,71,"17:40",38,62,5.4,"alc"],["Sep 12",7.5,1.02,8840,82,"13:30",69,56,12.8,""],
 ];
export const mockSeeded: SeededDay[] = seededSeed.map(([date,sleep_h,hrv_ratio,steps,sri,caffeine_last,recovery,resting_hr,run_km,journal])=>({date,sleep_h,hrv_ratio,steps,sri,caffeine_last:`bed ${caffeine_last}`,recovery,resting_hr,run_km,journal:journal||undefined,sources:seededSources}));

// A seated HR spike planted mid-window, the way the demo day is seeded (SPEC §14.3).
export const mockBiometrics = (): Biometrics => ({metric:"heart_rate",source:"apple_watch",points:Array.from({length:120},(_,i)=>{const t=now-600+i*5,spike=i>78&&i<104;return [t,Math.round(spike?96+Math.sin(i/3)*6+(i-78)*0.7:63+Math.sin(i/7)*4)] as [number,number];})});
// The rest of the SPEC §14.1 metric set, on the same 10-minute window as the
// HR strip, so mock mode renders every tile rather than a row of dashes.
const ramp = (n: number, step: number, at: (i: number) => number): [number, number][] =>
  Array.from({length: n}, (_, i) => [now - (n - 1 - i) * step, at(i)] as [number, number]);
const MOCK_SERIES: Record<string, {source: string; at: (i: number) => number; n: number; step: number}> = {
  hrv_rmssd: {source:"whoop", n:11, step:60, at:i=>Math.round(52-11*Math.min(1,Math.max(0,(i-4)/3))+Math.sin(i)*1.5)},
  spo2: {source:"apple_watch", n:11, step:60, at:i=>i===3?95:97+(i%3===1?1:0)},
  respiratory_rate: {source:"apple_watch", n:11, step:60, at:i=>i>5?17:14},
  wrist_temp_dev: {source:"whoop", n:11, step:60, at:i=>Math.round((0.12+0.05*Math.sin(i/3))*100)/100},
  strain: {source:"whoop", n:11, step:60, at:i=>Math.round((6.0+i*0.04)*10)/10},
  steps_delta: {source:"apple_watch", n:11, step:60, at:i=>i>7?88:0},
  env_sound_db: {source:"apple_watch", n:11, step:60, at:i=>i>4&&i<9?62:45},
};
export const mockBiometricsMulti = (metrics: string[]): BiometricsMulti => ({
  series: Object.fromEntries(metrics.map(metric => {
    if (metric === "heart_rate") { const hr = mockBiometrics(); return [metric, {source:hr.source, origin:"seed" as const, points:hr.points}]; }
    const spec = MOCK_SERIES[metric];
    return [metric, spec
      ? {source:spec.source, origin:"seed" as const, points:ramp(spec.n, spec.step, spec.at)}
      : {source:"", origin:"seed" as const, points:[] as [number, number][]}];
  })),
});
export const mockWearablesStatus: WearablesStatus = {
  metrics: [["heart_rate","apple_watch"],...Object.entries(MOCK_SERIES).map(([m,s])=>[m,s.source] as [string,string])]
    .map(([metric, source])=>({metric,source,origin:"seed" as const,count:120,last_t:now})),
  live_connected: false,
  live_devices: [],
  catalogue: {},
};
export const mockStatus: Status={demo_mode:true,source:"glasses",uptime_s:742,tick_count:742,ai_coverage:.77,t1_busy:false,dropped_escalations:1,last_tick_t:now,tick_interval_s:1.5,capture:{
  phone:{connected:1,received:748,latest_age_s:.4,latest_transit_ms:83},
  loop:"ticks=742 rate=0.667Hz ai=77% device=739/742 sync_mean=4.12ms sync_p50=3.86ms sync_p99=8.20ms slow=17",
  tagger:{model:"gemini-2.5-flash-lite",calls:581,p50_ms:842,p95_ms:1288,overruns:9,last_error:""},
  converted:742,dropped:2,ring:{count:58,bytes:4829341},
}};
export const mockSummary: TodaySummary={lines:["09:14 — focused screen work began in the office","10:47 — coffee with a colleague; social episode logged","12:31 — mixed lunch with two colleagues","14:08 — 27-minute park walk; vegetation visible","16:42 — late caffeine observed after cutoff"]};
export const mockPending: PendingCheck[]=[{id:"w1",due_t:now+480,reason:"Check whether screen break happened",trigger:"screen_sustained"},{id:"w2",due_t:now+900,reason:"Check whether meal has ended",trigger:"food_in_frame"}];
