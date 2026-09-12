import type { Decision, Episode, PendingCheck, Scores, SeededDay, Status, Tick, TodaySummary } from "./types";
const now = Date.now() / 1000;
const scenes = ["office","office","restaurant","restaurant","street","park","park","gym"];
export const mockTicks: Tick[] = Array.from({length:30},(_,i) => { const scene=scenes[Math.floor(i/4)%scenes.length]; const missing=i%5===0; return {v:1,tick_id:`t_${1740+i}`,t:now-29+i,seq:1740+i,sensor:{lux_proxy:scene==="park"?510:180,cct:4100,hist_spread:.62,frame_delta:.08+(i%4)*.04,flow_mag:.04,sharpness:88,phash:`e3a91c04b7d2${i.toString().padStart(4,"0")}`},device:{accel_rms:.04,gps_speed:scene==="street"?1.2:.2},...(!missing&&{ai:{as_of:now-29+i-.3,age_ms:300,scene,activity:scene==="gym"?"exercising":"seated",food_present:scene==="restaurant",food_type:scene==="restaurant"?"mixed":"none",caffeine_visible:i===12,alcohol_visible:i===15,screen_present:scene==="office",vegetation_visible:scene==="park",people_present:["restaurant","park"].includes(scene),conf:.86}}),frame_ref:`f_${1740+i}`}; });
const decisionSeed: Array<[string,string,string[],boolean,boolean,string|null]> = [
 ["food_in_frame","Mixed lunch with two colleagues, restaurant.",["annotate","log_insight"],false,false,null],
 ["late_caffeine","Coffee after personal cutoff may disrupt tonight’s sleep.",["annotate","speak","watch"],true,false,null],
 ["screen_sustained","Focused screen work has continued for 52 minutes.",["annotate","watch"],false,false,null],
 ["people_present","Relaxed conversation with two people.",["annotate"],false,false,null],
 ["alcohol_visible","Alcohol detected near bedtime; note recovery tomorrow.",["annotate","log_insight","speak"],true,false,null],
 ["outdoor_block","Green outdoor walk is accumulating nature minutes.",["annotate"],false,false,null],
 ["gym_session","Resistance training session underway.",["annotate","log_insight"],false,false,null],
 ["food_in_frame","Escalation arrived while reasoner was occupied.",[],false,true,"t1_busy — dropped, never queued"],
];
export const mockDecisions: Decision[] = decisionSeed.map((d,i)=>({id:`d_000${8-i}`,t:now-i*420,trigger:d[0],trigger_tick_id:`t_${1769-i}`,episode_id:i===7?null:`e_000${i+1}`,interpretation:d[1],confidence:i===7?0:.78+i*.02,actions:d[2].map(type=> type==="annotate"?{type,line:d[1]}:type==="log_insight"?{type,category:"health",text:d[1]}:type==="watch"?{type,after_s:900,reason:"re-check condition"}:{type:"speak",text:"A quick note from your health copilot.",urgency:"low"}) as Decision["actions"],spoke:d[3],dropped:d[4],drop_reason:d[5],latency_ms:i===7?0:940+i*137,model:i===7?"—":"gpt-5.4-mini"}));
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
export const mockSeeded: SeededDay[] = [
 ["Sep 06",7.8,1.08,9210,86,"13:10"],["Sep 07",7.4,1.03,8020,84,"14:05"],["Sep 08",6.1,.82,7140,72,"18:20"],["Sep 09",7.6,1.05,10340,85,"12:45"],["Sep 10",5.9,.76,6320,68,"19:05"],["Sep 11",6.2,.81,8420,71,"17:40"],["Sep 12",7.5,1.02,8840,82,"13:30"]
 ].map(([date,sleep_h,hrv_ratio,steps,sri,caffeine_last])=>({date,sleep_h,hrv_ratio,steps,sri,caffeine_last} as SeededDay));
export const mockStatus: Status={demo_mode:true,source:"sim",uptime_s:742,tick_count:742,ai_coverage:.77,t1_busy:false,dropped_escalations:1,last_tick_t:now};
export const mockSummary: TodaySummary={lines:["09:14 — focused screen work began in the office","10:47 — coffee with a colleague; social episode logged","12:31 — mixed lunch with two colleagues","14:08 — 27-minute park walk; vegetation visible","16:42 — late caffeine observed after cutoff"]};
export const mockPending: PendingCheck[]=[{id:"w1",due_t:now+480,reason:"Check whether screen break happened",trigger:"screen_sustained"},{id:"w2",due_t:now+900,reason:"Check whether meal has ended",trigger:"food_in_frame"}];
