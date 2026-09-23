/**
 * The studies behind the rules, one entry each: author, year, journal, DOI or
 * PMID, grade and the finding as one line of numbers. `CLAIMS` is the list of
 * things the app does not claim, each with what the studies say instead.
 * Grade A: a meta-analysis or a large trial. B: a controlled study or a large
 * cohort. C: a small or older study, or a protocol default with no study.
 */

export type Grade = "A" | "B" | "C";

export interface Source {
  key: string;
  author: string;
  year: number;
  journal: string;
  doi: string | null;
  pmid?: string;
  grade: Grade;
  /** The number, in one line. */
  finding: string;
}

const s = (source: Source): Source => source;

export const SOURCES: Record<string, Source> = {
  gardiner2023: s({
    key: "gardiner2023", author: "Gardiner", year: 2023, journal: "Sleep Med Rev", doi: "10.1016/j.smrv.2023.101764", grade: "A",
    finding: "Evening caffeine, pooled: total sleep −45 min, efficiency −7%, onset +9 min. The cutoff scales with dose: about 9 h before bed for ~100 mg, about 13 h for ~200 mg.",
  }),
  drake2013: s({
    key: "drake2013", author: "Drake", year: 2013, journal: "J Clin Sleep Med", doi: "10.5664/jcsm.3170", grade: "B",
    finding: "400 mg of caffeine 6 h before bed cut sleep by more than an hour, and subjects did not notice.",
  }),
  vandongen2003: s({
    key: "vandongen2003", author: "Van Dongen", year: 2003, journal: "Sleep", doi: null, pmid: "12683469", grade: "A",
    finding: "6 h a night for 14 nights performs like 1–2 nights of no sleep; sleepiness ratings plateau by day 2–3 while performance keeps falling.",
  }),
  lim2010: s({
    key: "lim2010", author: "Lim & Dinges", year: 2010, journal: "Psychol Bull", doi: "10.1037/a0018883", grade: "A",
    finding: "Attention effect of sleep deprivation g≈0.7.",
  }),
  ebrahim2013: s({
    key: "ebrahim2013", author: "Ebrahim", year: 2013, journal: "Alcohol Clin Exp Res", doi: "10.1111/acer.12006", grade: "A",
    finding: "Any evening dose of alcohol cuts REM in the first half of the night and fragments the second half.",
  }),
  gunn2018: s({
    key: "gunn2018", author: "Gunn", year: 2018, journal: "Addiction", doi: "10.1111/add.14404", grade: "A",
    finding: "Next morning at 0.00 BAC: attention g=0.47, psychomotor speed g=0.66, memory g≈0.6.",
  }),
  grosicki2026: s({
    key: "grosicki2026", author: "Grosicki", year: 2026, journal: "PLOS Digit Health", doi: "10.1371/journal.pdig.0001284", grade: "B",
    finding: "Per drink that night: resting HR +2.4–2.8 bpm, HRV −3.3–3.8 ms, over 5.1M nights.",
  }),
  stutz2019: s({
    key: "stutz2019", author: "Stutz", year: 2019, journal: "Sports Med", doi: "10.1007/s40279-018-1015-0", grade: "A",
    finding: "Moderate evening exercise does not hurt sleep.",
  }),
  leota2025: s({
    key: "leota2025", author: "Leota", year: 2025, journal: "Nat Commun", doi: "10.1038/s41467-025-58271-x", grade: "A",
    finding: "Vigorous exercise ending 2 h before bed delays sleep onset ~36 min; ending 4 h or more before bed, no effect.",
  }),
  chang2012: s({
    key: "chang2012", author: "Chang", year: 2012, journal: "Brain Res", doi: "10.1016/j.brainres.2012.02.068", grade: "A",
    finding: "Executive function g≈0.1 after one bout of exercise.",
  }),
  gu2020: s({
    key: "gu2020", author: "Gu", year: 2020, journal: "J Clin Endocrinol Metab", doi: "10.1210/clinem/dgaa354", grade: "B",
    finding: "Dinner at 22:00 vs 18:00 raised peak glucose 18% and the 4 h exposure 18%; next-morning fasting glucose unchanged.",
  }),
  chang2015: s({
    key: "chang2015", author: "Chang", year: 2015, journal: "PNAS", doi: "10.1073/pnas.1418490112", grade: "B",
    finding: "An e-reader 4 h before bed delayed melatonin 1.5 h, onset +10 min, REM −12 min.",
  }),
  han2024: s({
    key: "han2024", author: "Han", year: 2024, journal: "J Med Internet Res", doi: "10.2196/48356", grade: "A",
    finding: "Adult screen use and sleep quality correlate r≈0.28.",
  }),
  rangtell2016: s({
    key: "rangtell2016", author: "Rångtell", year: 2016, journal: "Sleep Med", doi: "10.1016/j.sleep.2016.06.016", grade: "B",
    finding: "6.5 h of daytime light (~570 lux) erased the evening screen effect.",
  }),
  allen2016: s({
    key: "allen2016", author: "Allen", year: 2016, journal: "Environ Health Perspect", doi: "10.1289/ehp.1510037", grade: "B",
    finding: "Cognitive scores −15% at 945 ppm CO2 and −50% at 1,400 ppm vs 550.",
  }),
  satish2012: s({
    key: "satish2012", author: "Satish", year: 2012, journal: "Environ Health Perspect", doi: "10.1289/ehp.1104789", grade: "B",
    finding: "Cognitive scores −15% at 945 ppm CO2 and −50% at 1,400 ppm vs 550 (paired with Allen 2016).",
  }),
  windred2024: s({
    key: "windred2024", author: "Windred", year: 2024, journal: "Sleep", doi: "10.1093/sleep/zsad253", grade: "A",
    finding: "Top vs bottom sleep-regularity quintile: 30% lower all-cause mortality; regularity beats duration as a predictor.",
  }),
  phillips2017: s({
    key: "phillips2017", author: "Phillips", year: 2017, journal: "Sci Rep", doi: "10.1038/s41598-017-03171-4", grade: "B",
    finding: "Irregular sleepers' melatonin onset 2.2 h later.",
  }),
  roenneberg2012: s({
    key: "roenneberg2012", author: "Roenneberg", year: 2012, journal: "Curr Biol", doi: "10.1016/j.cub.2012.03.038", grade: "B",
    finding: "Social jetlag, |midsleep weekday − free day|: each hour, +33% odds of overweight.",
  }),
  wright2013: s({
    key: "wright2013", author: "Wright", year: 2013, journal: "Curr Biol", doi: "10.1016/j.cub.2013.06.039", grade: "B",
    finding: "A week of natural light shifted the body clock 2 h earlier.",
  }),
  figueiro2017: s({
    key: "figueiro2017", author: "Figueiro", year: 2017, journal: "Sleep Health", doi: null, grade: "C",
    finding: "Morning light at work shortened sleep onset.",
  }),
  hunter2019: s({
    key: "hunter2019", author: "Hunter", year: 2019, journal: "Front Psychol", doi: "10.3389/fpsyg.2019.00722", grade: "B",
    finding: "20–30 min outdoors drops cortisol about 21% per hour beyond the normal decline.",
  }),
  ybarra2008: s({
    key: "ybarra2008", author: "Ybarra", year: 2008, journal: "Pers Soc Psychol Bull", doi: "10.1177/0146167207310454", grade: "B",
    finding: "10 min of talking raised processing speed and working memory as much as a puzzle session.",
  }),
  armstrong2012: s({
    key: "armstrong2012", author: "Armstrong", year: 2012, journal: "J Nutr", doi: "10.3945/jn.111.142000", grade: "B",
    finding: "1.4–1.6% body-water loss: fatigue, vigilance and mood down.",
  }),
  ganio2011: s({
    key: "ganio2011", author: "Ganio", year: 2011, journal: "Br J Nutr", doi: "10.1017/S0007114511002005", grade: "B",
    finding: "1.4–1.6% body-water loss: fatigue, vigilance and mood down.",
  }),
  brooks2006: s({
    key: "brooks2006", author: "Brooks & Lack", year: 2006, journal: "Sleep", doi: "10.1093/sleep/29.6.831", grade: "B",
    finding: "A 10–20 min nap helps for about 2.5 h with no grogginess; over 30 min costs ~41% on waking for 35–95 min.",
  }),
  laukkanen2017: s({
    key: "laukkanen2017", author: "Laukkanen", year: 2017, journal: "Age Ageing", doi: "10.1093/ageing/afw212", grade: "B",
    finding: "4–7 sauna sessions a week vs 1: dementia hazard 0.34 over 20 years. Long-term only; no same-day claim.",
  }),
  insana2013: s({
    key: "insana2013", author: "Insana", year: 2013, journal: "Sleep", doi: "10.5665/sleep.2304", grade: "B",
    finding: "Postpartum: slowest reaction times stayed worse for all 12 weeks and got worse from week 2 to 12 even as sleep improved.",
  }),
  monk2005: s({
    // The spec gives no journal or DOI; none is guessed (docs/SOURCES.md says the same).
    key: "monk2005", author: "Monk", year: 2005, journal: "", doi: null, grade: "C",
    finding: "The post-lunch dip is circadian and happens without lunch.",
  }),
  waziry2023: s({
    key: "waziry2023", author: "Waziry", year: 2023, journal: "Nat Aging", doi: "10.1038/s43587-022-00357-y", grade: "A",
    finding: "Two years of calorie restriction slowed DunedinPACE 2–3%.",
  }),
  belsky2022: s({
    key: "belsky2022", author: "Belsky", year: 2022, journal: "eLife", doi: null, grade: "A",
    finding: "DunedinPACE 1.0 = a normal pace of aging; Bryan's 0.7.",
  }),
  basner2011: s({
    key: "basner2011", author: "Basner", year: 2011, journal: "Acta Astronaut", doi: "10.1016/j.actaastro.2011.07.015", grade: "A",
    finding: "PVT-B, 3 min: mean RT, lapses over 355 ms, false starts.",
  }),
  dagum2018: s({
    key: "dagum2018", author: "Dagum", year: 2018, journal: "npj Digit Med", doi: "10.1038/s41746-018-0018-4", grade: "B",
    finding: "Typing dynamics r≈0.87 with a neuropsych battery.",
  }),
};

/** What we don't claim, and what the studies say instead. */
export const CLAIMS: { claim: string; truth: string; sources: string[] }[] = [
  {
    claim: "A flat 10 h caffeine rule.",
    truth: "The cutoff scales with dose: about 9 h before bed for one coffee (~100 mg), about 13 h for ~200 mg.",
    sources: ["gardiner2023", "drake2013"],
  },
  {
    claim: "A 6 h exercise rule.",
    truth: "Moderate evening exercise does not hurt sleep. Vigorous exercise ending 4 h or more before bed has no effect; ending 2 h before bed delays onset about 36 min.",
    sources: ["stutz2019", "leota2025"],
  },
  {
    claim: "Screens are the main cause of bad sleep.",
    truth: "Screen use and sleep quality correlate r≈0.28. An e-reader 4 h before bed cost 10 min of onset and 12 min of REM, and 6.5 h of daytime light erased the effect.",
    sources: ["han2024", "chang2015", "rangtell2016"],
  },
  {
    claim: "Naps under 20 min cost you tonight.",
    truth: "A 10–20 min nap helps for about 2.5 h with no grogginess. Over 30 min costs about 41% on waking for 35–95 min.",
    sources: ["brooks2006"],
  },
  {
    claim: "One drink has no next day.",
    truth: "Next morning at 0.00 BAC: attention g=0.47, psychomotor speed g=0.66, memory g≈0.6. Per drink that night, resting HR +2.4–2.8 bpm and HRV −3.3–3.8 ms.",
    sources: ["gunn2018", "grosicki2026"],
  },
  {
    claim: "Indoor CO2 only matters at extreme levels.",
    truth: "Cognitive scores −15% at 945 ppm and −50% at 1,400 ppm vs 550. It matters from 900 ppm.",
    sources: ["allen2016", "satish2012"],
  },
  {
    claim: "A late dinner raises fasting glucose.",
    truth: "Dinner at 22:00 vs 18:00 raised peak glucose 18% and the 4 h exposure 18%; next-morning fasting glucose was unchanged.",
    sources: ["gu2020"],
  },
  {
    claim: "Sleep duration matters more than regularity.",
    truth: "Regularity beats duration as a predictor: top vs bottom regularity quintile, 30% lower all-cause mortality.",
    sources: ["windred2024"],
  },
];

/** "Gardiner 2023 · Sleep Med Rev"; "Monk 2005" when no journal is on file. */
export function sourceLine(key: string): string {
  const source = SOURCES[key];
  if (!source) return key;
  return source.journal ? `${source.author} ${source.year} · ${source.journal}` : `${source.author} ${source.year}`;
}
