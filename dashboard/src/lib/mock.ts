import type { BiometricOrigin, Biometrics, BiometricsMulti, Decision, DecisionAction, Episode, Healthspan, HealthspanFactor, PendingCheck, Provenance, Question, Scores, SeededDay, SeededMetricRow, Status, Tick, TodaySummary, WearablesStatus } from "./types";
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
// One row per terminal state the questions panel renders: still waiting, answered
// and parsed, and refused by a guard (no phone in the room advertises `ask`).
export const mockQuestions: Question[] = [
 {id:"q_3f2a91c4",created_t:now-18,expires_t:now+9,decision_id:"d_0011",episode_id:"e_0006",question:"Is that drink yours?",answer_kind:"yes_no",fills:"confirmed",status:"open",answer_text:null,answer_t:null,heard:null,parsed:null,followup_of:null,sent_t:now-16,suppressed_reason:null},
 {id:"q_8b0d7e52",created_t:now-640,expires_t:now-613,decision_id:"d_0009",episode_id:"e_0003",question:"How many did you have?",answer_kind:"count",fills:"count",status:"answered",answer_text:"yeah, two",answer_t:now-631,heard:true,parsed:{understood:true,confirmed:true,count:2,food_type:null,note:"two beers",followup:null},followup_of:"q_1c55aa90",sent_t:now-638,suppressed_reason:null},
 {id:"q_1c55aa90",created_t:now-1820,expires_t:now-1793,decision_id:"d_0006",episode_id:"e_0003",question:"Was that a full meal or a snack?",answer_kind:"free",fills:"note",status:"expired",answer_text:null,answer_t:null,heard:false,parsed:null,followup_of:null,sent_t:now-1818,suppressed_reason:null},
 {id:"q_44e1b30f",created_t:now-2450,expires_t:null,decision_id:"d_0004",episode_id:"e_0002",question:"Is that your second coffee?",answer_kind:"yes_no",fills:"confirmed",status:"suppressed",answer_text:null,answer_t:null,heard:null,parsed:{},followup_of:null,sent_t:null,suppressed_reason:"ask_unsupported"},
];
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

/** `GET /api/seeded?days=2`, long format — what `api.seededRows` reads.
 *
 * Shaped like a real connected Fitbit: the day-scoped numbers land under
 * today, and last night's sleep block lands under **yesterday**, which is the
 * fallback the wearable strip has to cover. The non-fitbit rows are there on
 * purpose — the strip must refuse to call an `apple_watch` row live while the
 * only connected device is a Fitbit.
 */
const isoDay = (back: number) => { const d = new Date((now - back * 86400) * 1000);
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`; };
const fitbitToday: Array<[string, number, string]> = [
  ["hrv_rmssd_ms",41,"ms"],["spo2",96,"%"],["respiratory_rate",15.4,"brpm"],["skin_temp_c",33.3,"degC"],["resting_hr",58,"bpm"],
];
const fitbitLastNight: Array<[string, number, string]> = [
  ...fitbitToday,
  ["sleep_hours",6.4,"h"],["deep_min",54,"min"],["rem_min",81,"min"],["bed_time",23.75,"h"],["wake_time",6.4,"h"],
];
export const mockSeededRows: SeededMetricRow[] = [
  ...fitbitToday.map(([metric,value,unit])=>({day:isoDay(0),metric,value,unit,source:"fitbit"})),
  ...fitbitLastNight.map(([metric,value,unit])=>({day:isoDay(1),metric,value,unit,source:"fitbit"})),
  {day:isoDay(0),metric:"steps",value:8840,unit:"count",source:"apple_watch"},
  {day:isoDay(0),metric:"sleep_regularity_sri",value:82,unit:"",source:"oura"},
  {day:isoDay(1),metric:"steps",value:8420,unit:"count",source:"apple_watch"},
];

// A seated HR spike planted mid-window, the way the demo day is seeded (SPEC §14.3).
export const mockBiometrics = (): Biometrics => ({metric:"heart_rate",source:"fitbit",origin:"live",points:Array.from({length:120},(_,i)=>{const t=now-600+i*5,spike=i>78&&i<104;return [t,Math.round(spike?96+Math.sin(i/3)*6+(i-78)*0.7:63+Math.sin(i/7)*4)] as [number,number];})});
// The rest of the SPEC §14.1 metric set, on the same 10-minute window as the
// HR strip, so mock mode renders every tile rather than a row of dashes.
const ramp = (n: number, step: number, at: (i: number) => number): [number, number][] =>
  Array.from({length: n}, (_, i) => [now - (n - 1 - i) * step, at(i)] as [number, number]);
// `live: true` is what a connected Fitbit streams intraday. The rest stay
// seeded so mock mode walks every branch of the strip's tile resolution:
// intraday live (steps), live daily row (HRV/SpO2/RR/temp), and
// "the device does not measure this at all" (strain, sound).
const MOCK_SERIES: Record<string, {source: string; at: (i: number) => number; n: number; step: number; live?: boolean}> = {
  hrv_rmssd: {source:"whoop", n:11, step:60, at:i=>Math.round(52-11*Math.min(1,Math.max(0,(i-4)/3))+Math.sin(i)*1.5)},
  spo2: {source:"apple_watch", n:11, step:60, at:i=>i===3?95:97+(i%3===1?1:0)},
  respiratory_rate: {source:"apple_watch", n:11, step:60, at:i=>i>5?17:14},
  wrist_temp_dev: {source:"whoop", n:11, step:60, at:i=>Math.round((0.12+0.05*Math.sin(i/3))*100)/100},
  strain: {source:"whoop", n:11, step:60, at:i=>Math.round((6.0+i*0.04)*10)/10},
  steps_delta: {source:"fitbit", n:11, step:60, at:i=>i>7?88:0, live:true},
  env_sound_db: {source:"apple_watch", n:11, step:60, at:i=>i>4&&i<9?62:45},
};
export const mockBiometricsMulti = (metrics: string[]): BiometricsMulti => ({
  series: Object.fromEntries(metrics.map(metric => {
    if (metric === "heart_rate") { const hr = mockBiometrics(); return [metric, {source:hr.source, origin:"live" as const, points:hr.points}]; }
    const spec = MOCK_SERIES[metric];
    return [metric, spec
      ? {source:spec.source, origin:(spec.live?"live":"seed") as BiometricOrigin, points:ramp(spec.n, spec.step, spec.at)}
      : {source:"", origin:"seed" as BiometricOrigin, points:[] as [number, number][]}];
  })),
});
export const mockWearablesStatus: WearablesStatus = {
  metrics: ([["heart_rate","fitbit",true],...Object.entries(MOCK_SERIES).map(([m,spec])=>[m,spec.source,!!spec.live] as [string,string,boolean])] as Array<[string,string,boolean]>)
    .map(([metric, source, isLive])=>({metric,source,origin:(isLive?"live":"seed") as BiometricOrigin,count:120,last_t:now})),
  live_connected: true,
  live_devices: ["fitbit"],
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

// Late Saturday 2026-09-12 as `/api/healthspan` reports it (design §C; engine
// numbers from the seeded DB). State-exhaustive so mock mode renders every
// branch: one factor of each provenance, all three ledger statuses, all five
// insight kinds (the `you` line needs the ≥14-day history the seeded DB cannot
// give, so `effects[0]` is the 30-day state), a credit and a debit pin. Frozen
// at 14:32 on purpose — no wall clock anywhere in this payload.
const CITE = {
 steps:"Paluch 2022 Lancet Public Health (15 cohorts, n=47,471): vs ~3.5k, Q2 5.8k HR 0.60, Q3 7.8k 0.55, Q4 10.9k 0.47; plateau 8–10k under 60, 6–8k over 60",
 vilpa:"Stamatakis 2022 Nature Medicine (UK Biobank, n=25,241): 3–4 min/day of vigorous bursts → 26–30% lower all-cause mortality",
 resistance:"Momma 2022 BJSM meta-analysis (16 studies): 30–60 min/wk → 10–17% lower mortality; J-shaped above ~130 min",
 fitness:"Mandsager 2018 JAMA Netw Open (n=122,007): low vs elite fitness adjusted HR 5.04; no upper limit of benefit. Curve here is deliberately compressed",
 gait:"Studenski 2011 JAMA (pooled n=34,485): each +0.1 m/s ≈ HR 0.88; Dunedin: gait speed at 45 tracks biological aging",
 sleep:"Cappuccio 2010 Sleep meta-analysis (16 studies, n=1.3M): short sleep RR 1.12, long sleep RR 1.30",
 sri:"Windred 2024 Sleep (UK Biobank, n=60,977): top vs bottom SRI quintile 20–48% lower all-cause mortality; regularity outperformed duration",
 dayLight:"Windred 2024 PNAS (n=88,905, wrist sensors): darkest-day deciles → ~15–20% higher mortality; Brown 2022 consensus: ≥250 lx melanopic by day",
 nightLight:"Windred 2024 PNAS: brightest-night deciles → 21–34% higher mortality; Brown 2022: ≤1 lx melanopic during sleep",
 social:"Holt-Lunstad 2010 PLOS Med (148 studies, n=308,849): stronger ties OR 1.50 for survival; complex integration OR 1.91",
 purpose:"Alimujiang 2019 JAMA Netw Open (HRS, n=6,985): lowest vs highest purpose HR 2.43",
 nature:"White 2019 Sci Rep (n≈20k): ≥120 min/wk → better health/wellbeing; Rojas-Rueda 2019 Lancet Planet Health: 4% lower mortality per 0.1 NDVI",
 noise:"WHO 2018 Environmental Noise Guidelines: Lnight <45 dB; IHD RR ~1.08 per 10 dB Lden",
 med:"Sofi 2010 AJCN meta-analysis: each 2-point adherence gain → 8% lower mortality; PREDIMED RCT: ~30% fewer major CV events",
 alcohol:"Zhao 2023 JAMA Netw Open (107 studies): no protective range once abstainer bias is removed; risk rises from ~2 drinks/day",
 smoker:"Jha 2013 NEJM (n=201,551): smokers lose ≥10 years; quitting before 40 removes ~90% of the excess",
 sauna:"Laukkanen 2015 JAMA IM (n=2,315 Finnish men, 20.7 y): 4–7×/wk vs 1× HR 0.60 all-cause; sessions >19 min",
 hrv:"Low HRV is a marker of autonomic strain (Framingham, Tsuji 1996); used here as a state marker, not a cause",
 drake:"Drake 2013 J Clin Sleep Med RCT: 400 mg caffeine 6 h before bed cut total sleep time by >1 h",
};
// Engine order (brian_score.FACTORS): key, layer, label, dose, hr, hours, grade, source, provenance, basis, detail.
const healthspanFactorRows: Array<[string,string,string,number|null,number|null,number,string,string,Provenance,string,string]> = [
 ["steps","Movement","Daily steps",6100,.617,.24,"A_cohort",CITE.steps,"seeded","phone","phone steps, row 2026-09-12"],
 ["vilpa_min","Movement","Vigorous bursts",1.8,.824,.23,"A_cohort",CITE.vilpa,"seeded","phone","phone vilpa_minutes, row 2026-09-12"],
 ["resistance_min_wk","Movement","Strength training",0,1,-.29,"A_cohort",CITE.resistance,"derived","glasses","0 min over 6 covered days — all gym minutes counted as resistance (no lifting/cardio split from the camera)"],
 ["fitness_pct","Movement","Cardiorespiratory fitness",77.5,.444,.97,"A_cohort",CITE.fitness,"derived","apple_watch","VO2max 51.0 → ~78th percentile, M 20s (coarse norms, ±10)"],
 ["gait_speed","Movement","Walking speed",1.19,.486,-.02,"A_cohort",CITE.gait,"seeded","phone","phone gait_speed_ms, row 2026-09-12"],
 ["sleep_hours","Sleep","Sleep duration",6.2,1.08,-.27,"A_cohort",CITE.sleep,"seeded","whoop","whoop row for the night starting 2026-09-12"],
 ["sri","Sleep","Sleep regularity",63,.842,-.31,"A_cohort",CITE.sri,"seeded","whoop","whoop row for the night starting 2026-09-12"],
 ["day_light_min","Light & clock","Bright light minutes",26,.931,-.1,"A_cohort",CITE.dayLight,"seeded","phone","phone daytime_light_minutes, row 2026-09-12"],
 ["night_light_lux","Light & clock","Light during sleep",1,1,.09,"A_cohort",CITE.nightLight,"derived","phone","proxy: evening_light_ok=1 → 1 lx (2-point map)"],
 ["social_index","Social","Social integration",60,.68,.2,"A_cohort",CITE.social,"derived","glasses","44 min of conversation across 2 encounters (breadth proxy: encounters, not identities)"],
 ["purpose","Social","Purpose in life",4.75,.662,.14,"B",CITE.purpose,"derived","user","purpose_score 4 of 5 rescaled to 4.75 on the 1–6 scale"],
 ["nature_min_wk","Environment","Time in nature",84,.972,.02,"B",CITE.nature,"live","glasses","84 min in park over 6 covered days (2026-09-06..2026-09-12)"],
 ["noise_night_db","Environment","Night noise",44,1,0,"B",CITE.noise,"seeded","phone","phone night_noise_db, row 2026-09-12"],
 ["med_adherence","Diet & substances","Mediterranean pattern",1,.86,.27,"A_cohort",CITE.med,"live","glasses","1/1 typed meals on-pattern (0 untyped ignored; the §8 scorer counts untyped as off-pattern)"],
 ["alcohol_drinks","Diet & substances","Alcohol",0,1,.03,"A_cohort",CITE.alcohol,"live","glasses","no alcohol sighting today (glasses worn, 6 episodes)"],
 ["smoker","Diet & substances","Smoking / vaping nicotine daily",null,null,0,"A_cohort",CITE.smoker,"missing","whoop","no journal_nicotine row for 2026-09-12"],
 ["sauna_wk","Recovery","Sauna sessions",0,1,0,"B",CITE.sauna,"live","glasses","0 sessions >19 min over 6 covered days"],
 ["recovery_ratio","Recovery","HRV vs your baseline",.86,1.047,-.11,"B",CITE.hrv,"seeded","whoop","whoop row for the night starting 2026-09-12"],
];
const healthspanFactors: HealthspanFactor[] = healthspanFactorRows.map(([key,layer,label,dose,hr,hours,grade,source,provenance,basis,detail])=>({key,layer,label,dose,hr,hours,grade,measured:provenance!=="missing",source,provenance,basis,detail}));
export const mockHealthspan: Healthspan = {
 day:"2026-09-12", as_of_hh:14.53, engine:"brian_score",
 overall:76, layers:{"Movement":81,"Sleep":65,"Light & clock":80,"Social":69,"Environment":79,"Diet & substances":100,"Recovery":8},
 years_delta:2.11, years_ci:[-1.72,5.95], hours_today:.91, hours_ci:[-.74,2.56],
 measured:{count:17,total:18},
 factors: healthspanFactors,
 ledger:[
  {key:"nature_min_wk",label:"Time in nature",accrued:84,target:120,projected:98,deficit:22,days_elapsed:6,status:"at_risk"},
  {key:"resistance_min_wk",label:"Strength training",accrued:0,target:60,projected:0,deficit:60,days_elapsed:6,status:"behind"},
  {key:"sauna_wk",label:"Sauna sessions",accrued:0,target:2.5,projected:0,deficit:2.5,days_elapsed:6,status:"behind"},
  {key:"steps",label:"Daily steps",accrued:7600,target:8000,projected:7600,deficit:400,days_elapsed:6,status:"at_risk"},
  {key:"day_light_min",label:"Bright light minutes",accrued:31.7,target:45,projected:31.7,deficit:13.3,days_elapsed:6,status:"at_risk"},
  {key:"vilpa_min",label:"Vigorous bursts",accrued:2.9,target:4,projected:2.9,deficit:1.1,days_elapsed:6,status:"at_risk"},
  {key:"social_index",label:"Social integration",accrued:72,target:70,projected:72,deficit:0,days_elapsed:6,status:"on_track"},
 ],
 forecast:{sleep_hours:6.34,hrv_change_pct:0,sri_change_pts:-21,melatonin_delay_min:0,drivers:["caffeine at 16:00 is inside your 9 h cutoff","bedtime +105 min vs habit"]},
 levers:[
  {key:"vilpa_min",label:"Vigorous bursts",action:"Vigorous bursts: 1.8 → 4.8 min/day",hours_gain:.596,time_min:3,roi_hours_per_min:.199,layers:["movement"],source:CITE.vilpa},
  {key:"bundle_walk",label:"Outdoor walk with someone before 10:00",action:"30-min outdoor walk with a friend before 10:00",hours_gain:.858,time_min:30,roi_hours_per_min:.0286,layers:["environment","light","movement","social"],source:"Bundles steps, bright light, nature, social — one act, four layers"},
  {key:"steps",label:"Daily steps",action:"Daily steps: 6100 → 8100 steps",hours_gain:.413,time_min:20,roi_hours_per_min:.0207,layers:["movement"],source:CITE.steps},
  {key:"social_index",label:"Social integration",action:"Social integration: 60 → 75 index 0–100",hours_gain:.322,time_min:20,roi_hours_per_min:.0161,layers:["social"],source:CITE.social},
  {key:"resistance_min_wk",label:"Strength training",action:"Strength training: 0 → 30 min/week",hours_gain:.445,time_min:30,roi_hours_per_min:.0148,layers:["movement"],source:CITE.resistance},
 ],
 levers_free:[
  {key:"sri",label:"Sleep regularity",action:"Sleep regularity: 63 → 73 SRI 0–100",hours_gain:.257,time_min:0,roi_hours_per_min:.257,layers:["sleep"],source:CITE.sri},
  {key:"purpose",label:"Purpose in life",action:"Purpose in life: 4.75 → 5.75 1–6",hours_gain:.195,time_min:0,roi_hours_per_min:.195,layers:["social"],source:CITE.purpose},
 ],
 insights:[
  {kind:"tonight",text:"Tonight: ~6.3 h of sleep, HRV +0%. Because: caffeine at 16:00 is inside your 9 h cutoff; bedtime +105 min vs habit.",source:CITE.drake},
  {kind:"today",text:"Today nets +0.9 healthy-life hours (±1.7): +1.0 h cardiorespiratory fitness, +0.3 h mediterranean pattern, -0.3 h sleep regularity, -0.3 h strength training.",source:"Gompertz shift + microlife framing (Spiegelhalter & Blastland, BMJ 2012)"},
  {kind:"week",text:"Daily steps: 7600 so far, on pace for 7600 vs target 8000. You need 400 more by Sunday.",source:CITE.steps},
  {kind:"lever",text:"Best use of your next 3 minutes: Vigorous bursts: 1.8 → 4.8 min/day. ≈ +0.6 healthy-life hours (11.9 h per hour invested).",source:CITE.vilpa},
  {kind:"you",text:"On your own data (30 days): each unit of late caffeine seen by the glasses (0/1) moves sleep hours that night by -0.920 [90% CI -1.410, -0.430].",source:"Lagged regression with weekday and strain covariates"},
 ],
 pins:[
  {time:"08:15",img:null,grade:"B",kind:"credit",seen:"Caffeine at 08:15",effect:"outside your 9 h cutoff — fine"},
  {time:"11:30",img:null,grade:"A",kind:"credit",seen:"Conversation, 22 min",effect:"counts toward social integration · +0.2 h"},
  {time:"12:30",img:null,grade:"A",kind:"credit",seen:"Meal: grains",effect:"tagged against the Mediterranean pattern"},
  {time:"15:30",img:null,grade:"A",kind:"credit",seen:"Conversation, 22 min",effect:"counts toward social integration · +0.2 h"},
  {time:"16:30",img:null,grade:"B",kind:"debit",seen:"Caffeine at 16:30",effect:"inside your 9 h cutoff (bed 00:45) — ~−1 h of sleep tonight"},
  {time:"18:30",img:null,grade:"A",kind:"credit",seen:"Outdoors, park",effect:"nature +12 min this week · bright light already counted by the phone"},
 ],
 observations:{steps:6100,vilpa_min:1.8,gait_speed:1.19,sleep_hours:6.2,sri:63,noise_night_db:44,recovery_ratio:.86,fitness_pct:77.5,day_light_min:26,night_light_lux:1,purpose:4.75,social_index:60,med_adherence:1,alcohol_drinks:0,last_caffeine_hh:16.5,night_screen_min:0,nature_min_wk:84,resistance_min_wk:0,sauna_wk:0,planned_bed_shift_min:105},
 provenance:{
  ...Object.fromEntries(healthspanFactors.map(f=>[f.key,{source:f.provenance,basis:f.basis,detail:f.detail}])),
  last_caffeine_hh:{source:"live",basis:"glasses",detail:"latest caffeine sighting today started 16:30"},
  night_screen_min:{source:"live",basis:"glasses",detail:"0 min of screen_block overlapping 22:00–05:00 (6 episodes today)"},
  planned_bed_shift_min:{source:"derived",basis:"whoop",detail:"tonight 00:45 vs habit 23:00 (lower median of 6 prior nights)"},
  bedtime_hh:{source:"seeded",basis:"whoop",detail:"bed_time row 2026-09-12 → 00:45"},
  baseline_sleep_h:{source:"derived",basis:"whoop",detail:"mean of 6 prior nights (2026-09-06..2026-09-11), today excluded"},
 },
 effects:[{exposure:"late caffeine seen by the glasses (0/1)",outcome:"sleep hours that night",beta:-.92,ci:[-1.41,-.43],n:30,blended_beta:-.93,note:"personal estimate"}],
 profile:{age:20,sex:"M",goal:"average",cyp1a2_slow:false,height_m:null,bedtime_hh:24.75,bedtime_source:"seeded"},
 baseline_sleep_h:6.92,
 window:{ledger_days:["2026-09-07","2026-09-08","2026-09-09","2026-09-10","2026-09-11","2026-09-12"],factor_days:["2026-09-06","2026-09-07","2026-09-08","2026-09-09","2026-09-10","2026-09-11","2026-09-12"],uncovered_days:[],days_elapsed:6},
 conventions:[
  "Night rows (sleep_hours, sri, hrv_rmssd_ratio, night_noise_db, evening_light_ok, bed_time) for day D describe the night that starts on D — same row the §8 scorer uses.",
  "Weekly hazard doses use the trailing 7 days; the ledger uses the ISO week to date.",
  "Unmeasured factors are imputed at the population reference and earn nothing.",
  "Untyped meals are excluded from the Mediterranean share (the §8 scorer counts them as off-pattern).",
  "Alcohol sightings within 30 min are one drink; a journal '1' is read as one drink.",
 ],
};
