# Sources

One line per source: Author Year · Journal · DOI (or PMID) · grade · finding · what the app does with it.
Keys in brackets match src/lib/sources.ts. Where the spec gives no DOI or journal, the line says so rather than guessing.

## Caffeine (caffeine)
- [gardiner2023] Gardiner 2023 · Sleep Med Rev · 10.1016/j.smrv.2023.101764 · A · Pooled effect of evening caffeine: total sleep −45 min, efficiency −7%, onset +9 min; cutoff scales with dose, ~100 mg needs about 9 h before bed, ~200 mg about 13 h · caffeineEnd = bedtime − 9 h (13:30 for a 22:30 bed); caffeineEndStrong = bedtime − 13 h (9:30) for doubles and energy drinks; sleepMinutes 45; decay 1 day.
- [drake2013] Drake 2013 · J Clin Sleep Med · 10.5664/jcsm.3170 · B · 400 mg 6 h before bed cut sleep by more than an hour and subjects did not notice · Consequence line "Half of it is still in you at bedtime: about 45 minutes less sleep, and you won't feel it."

## Chronic short sleep (sleep_debt)
- [vandongen2003] Van Dongen 2003 · Sleep · PMID 12683469 · A · 6 h a night for 14 nights performs like 1–2 nights of no sleep; sleepiness ratings plateau by day 2–3 while performance keeps falling · sleepDebtHours sums hours below 8 h over the last 14 nights; shortSleepDay counts consecutive nights under 7 h and fills N in "You feel fine. Your reaction time says day N of short sleep."
- [lim2010] Lim & Dinges 2010 · Psychol Bull · 10.1037/a0018883 · A · Attention effect of sleep deprivation g≈0.7 · Scales the sleep_debt cognition effect with hours of debt from the first hour (0.4% an hour, capped at 20 h): grey under 2 h, watch 2–6 h, violation over 6 h.

## Alcohol (alcohol)
- [ebrahim2013] Ebrahim 2013 · Alcohol Clin Exp Res · 10.1111/acer.12006 · A · Any evening dose cuts REM in the first half of the night and fragments the second half · Any evening drink is a violation regardless of dose.
- [gunn2018] Gunn 2018 · Addiction · 10.1111/add.14404 · A · Next morning at 0.00 BAC: attention g=0.47, psychomotor speed g=0.66, memory g≈0.6 · Next-day cognition effect is measured; the day 2 residual is our assumption and is labelled "assumed".
- [grosicki2026] Grosicki 2026 · PLOS Digit Health · 10.1371/journal.pdig.0001284 · B · Per drink that night: resting HR +2.4–2.8 bpm, HRV −3.3–3.8 ms, 5.1M nights · Seeded nights apply rhr +2.6 bpm and hrv −3.5 ms per drink (the midpoints, labelled assumed in the rule); body effect scaled per drink.

## Exercise timing (exercise_timing)
- [stutz2019] Stutz 2019 · Sports Med · 10.1007/s40279-018-1015-0 · A · Moderate evening exercise does not hurt sleep · Moderate exercise at any time is green.
- [leota2025] Leota 2025 · Nat Commun · 10.1038/s41467-025-58271-x · A · Vigorous exercise ending 2 h before bed delays sleep onset ~36 min; ending 4 h or more before bed, no effect · Vigorous done 4 h or more before bed (moveBy 18:30) green; 2–4 h before bed (moveAmberUntil 20:30) amber, sleepMinutes 18 (half of 36, labelled assumed); under 2 h red, sleepMinutes 36.
- [chang2012] Chang 2012 · Brain Res · 10.1016/j.brainres.2012.02.068 · A · Executive function g≈0.1 after one bout · Named in the green workout line ("A small lift in executive function after"); no score bonus is applied.

## Late dinner (last_meal)
- [gu2020] Gu 2020 · J Clin Endocrinol Metab · 10.1210/clinem/dgaa354 · B · Dinner at 22:00 vs 18:00 raised peak glucose 18% and the 4 h exposure 18%; next-morning fasting glucose unchanged · eatingEnd = bedtime − 3.5 h (19:00); violation when a meal starts later; sleepMinutes 0; consequence "Digesting at bedtime: glucose runs about 18% higher through the night."

## Screens (screens, phone_in_bed)
- [chang2015] Chang 2015 · PNAS · 10.1073/pnas.1418490112 · B · An e-reader 4 h before bed delayed melatonin 1.5 h, onset +10 min, REM −12 min · A screen running past screensOff (21:30) is amber with sleepMinutes 22 (= onset +10, REM −12); phone in bed is red with sleepMinutes 45 assumed.
- [han2024] Han 2024 · J Med Internet Res · 10.2196/48356 · A · Adult screen use and sleep quality correlate r≈0.28 · Keeps the screens effect modest; screens are not framed as the main cause of bad sleep.
- [rangtell2016] Rångtell 2016 · Sleep Med · 10.1016/j.sleep.2016.06.016 · B · 6.5 h of daytime light (~570 lux) erased the evening screen effect · Insight line "daylight buys screen tolerance".

## Indoor CO2 (co2)
- [allen2016] Allen 2016 · Environ Health Perspect · 10.1289/ehp.1510037 · B · Cognitive scores −15% at 945 ppm and −50% at 1,400 ppm vs 550 · co2Amber = 900 ppm, watch, "Decisions run about 15% lower in this air. Open a window."; co2Red = 1,200 ppm, violation, "Decisions run 15 to 50% lower in this air (−15% at 945 ppm, −50% at 1,400). Open a window."
- [satish2012] Satish 2012 · Environ Health Perspect · 10.1289/ehp.1104789 · B · Cognitive scores −15% at 945 ppm and −50% at 1,400 ppm vs 550 (paired with Allen 2016) · Second source for the same thresholds.

## Sleep regularity (sleep_regularity)
- [windred2024] Windred 2024 · Sleep · 10.1093/sleep/zsad253 · A · Top vs bottom regularity quintile, 30% lower all-cause mortality; regularity beats duration as a predictor · sleepRegularityIndex 0–100 over the last 7 nights (1 − mean |Δbed| + |Δwake| relative to 240 min, clamped): our own drift score, not Windred's SRI, and labelled so; bed within regularityTolerance 30 min of target = green, otherwise watch.
- [phillips2017] Phillips 2017 · Sci Rep · 10.1038/s41598-017-03171-4 · B · Irregular sleepers' melatonin onset 2.2 h later · Backs the regularity consequence; effect 0.005 assumed.

## Social jetlag (social_jetlag)
- [roenneberg2012] Roenneberg 2012 · Curr Biol · 10.1016/j.cub.2012.03.038 · B · Social jetlag = |midsleep weekday − free day|; each hour, +33% odds of overweight · socialJetlagMinutes over the last 7 nights; over socialJetlagAmber 60 min = watch.

## Morning light (morning_light)
- [wright2013] Wright 2013 · Curr Biol · 10.1016/j.cub.2013.06.039 · B · A week of natural light shifted the body clock 2 h earlier · Rule: 10 min outside within 1 h of waking.
- [figueiro2017] Figueiro 2017 · Sleep Health · no DOI on file · C · Morning light at work shortened sleep onset · Second source for the morning-light rule; no number claimed.

## Nature (nature)
- [hunter2019] Hunter 2019 · Front Psychol · 10.3389/fpsyg.2019.00722 · B · 20–30 min outdoors drops cortisol about 21% per hour beyond the normal decline · Consequence line for the daylight target; 60 min by sunset stays the protocol default.

## Conversation (conversation)
- [ybarra2008] Ybarra 2008 · Pers Soc Psychol Bull · 10.1177/0146167207310454 · B · 10 min of talking raised processing speed and working memory as much as a puzzle session · Consequence line for the people target (30 min by 21:00).

## Hydration (hydration)
- [armstrong2012] Armstrong 2012 · J Nutr · 10.3945/jn.111.142000 · B · 1.4–1.6% body-water loss: fatigue, vigilance and mood down · Consequence line for the water target (2 L by 18:00).
- [ganio2011] Ganio 2011 · Br J Nutr · 10.1017/S0007114511002005 · B · 1.4–1.6% body-water loss: fatigue, vigilance and mood down · Second source for the water target.

## Naps (nap)
- [brooks2006] Brooks & Lack 2006 · Sleep · 10.1093/sleep/29.6.831 · B · 10–20 min helps for about 2.5 h with no grogginess; over 30 min costs ~41% on waking for 35–95 min · Inside when 13:00–15:00 and 20 min or less; watch over 30 min anywhere, "Over 30 min: about 41% worse on waking, for 35–95 min."; violation from 16:00, sleepMinutes 30 assumed, so the rule's claim is "assumed".

## Sauna (sauna)
- [laukkanen2017] Laukkanen 2017 · Age Ageing · 10.1093/ageing/afw212 · B · 4–7 sessions a week vs 1: dementia hazard 0.34 over 20 years · Long-term claim only; effect 0; no same-day claim.

## Postpartum (postpartum)
- [insana2013] Insana 2013 · Sleep · 10.5665/sleep.2304 · B · Slowest reaction times stayed worse for all 12 weeks and got worse from week 2 to 12 even as sleep improved · Nights with baby wakings (NightWaking.baby) are scored with a neutral tone and labelled "Not your decision."

## Skipped meal (skipped_meal)
- [monk2005] Monk 2005 · no journal on file · no DOI on file · C · Post-lunch dip is circadian and happens without lunch · A skipped meal is a grey X: effect 0, claim "none", consequence "Post-lunch dip is circadian and happens without lunch."

## Aging pace (not a rule)
- [waziry2023] Waziry 2023 · Nat Aging · 10.1038/s43587-022-00357-y · A · Two years of calorie restriction slowed DunedinPACE 2–3% · Scale for the pace figure (DunedinPACE 1.0 = normal; Bryan's 0.7).
- [belsky2022] Belsky 2022 · eLife · no DOI on file · A · DunedinPACE 1.0 = normal · Defines the pace metric.

## Mind test (not a rule)
- [basner2011] Basner 2011 · Acta Astronaut · 10.1016/j.actaastro.2011.07.015 · A · PVT-B, 3 min; metrics mean RT, lapses over 355 ms, false starts · The 3 min mind test reports exactly those three metrics.
- [dagum2018] Dagum 2018 · npj Digit Med · 10.1038/s41746-018-0018-4 · B · Typing dynamics r≈0.87 with a neuropsych battery · Backs the typing-dynamics signal in the mind test.

## Protocol defaults (no study)
- peptide, uv, eating_window, food_quality, sedentary, stress, air, sleep_deep, wake_anchor · grade C · window text says "protocol default" · effect small · claim "none".

## What we don't claim
- No flat 10 h caffeine rule; the cutoff scales with dose, 9 h for one coffee and 13 h for a double · gardiner2023, drake2013
- No 6 h exercise rule; vigorous exercise 4 h or more before bed has no effect, and moderate exercise any time is fine · leota2025, stutz2019
- Screens are not the main cause of bad sleep; the adult correlation is r≈0.28 and daytime light erases the evening effect · han2024, chang2015, rangtell2016
- Naps under 20 min are fine; the cost starts over 30 min · brooks2006
- One drink has a measurable next day: attention, psychomotor speed, memory, resting HR and HRV · gunn2018, grosicki2026, ebrahim2013
- CO2 matters from 900 ppm, not only in extreme rooms · allen2016, satish2012
- Late dinner does not raise fasting glucose; it raises the overnight exposure · gu2020
- Regularity beats duration as a predictor · windred2024, phillips2017
