# Nudges — everything Bryan may say in your ear

Every spoken line is an object: `{time, category, trigger, frame, said, evidence, escalation, outcome}`. It renders in "What Bryan said" and, if unspoken, in "Held back". The dashboard shows both.

## Global rules (the engine enforces these before any speech)
1. **Silence is default.** Escalation ladder: `annotate` (log only) → `dashboard` (tile/pin) → `speak`. Most triggers stop at `annotate`.
2. **One spoken line per 60 minutes**, max 6 per day. Never during a detected conversation, never while walking speed > 1.6 m/s near traffic, never in quiet hours (default 22:30–07:00 except the wind-down line).
3. **Say the object, the number, the cheapest fix.** ≤ 22 words. No greeting, no name, no "I noticed".
4. **Evidence grade gates tone.** Grade A can state consequence ("costs about an hour of sleep"). Grade B says "usually". Grade C never speaks; it only annotates.
5. **Never shame food or bodies.** Name the item, offer the swap, stop. No "again", no "should", no calorie counts.
6. **Cooldown per category** in the table. A category that spoke and wasn't acted on twice in a week drops to `dashboard` for that week (adherence bandit).
7. Every line ends with an outcome check 24 h later: `Did it` / `Didn't` / `Pending`, auto-detected from the glasses or WHOOP.

## Categories

| Category | Trigger (engine) | Cooldown | Escalates to | Evidence |
|---|---|---|---|---|
| **Fuel** | ultra-processed item in frame ≥ 2nd time today; meal after bedtime − 2 h; no vegetables seen by 18:00 | 4 h | dashboard → speak on 3rd | PREDIMED · Sofi 2010 (A) |
| **Caffeine** | caffeine sighting after bedtime − 9 h (−12 h if CYP1A2 slow) | 6 h | speak | Drake 2013 (B) |
| **Alcohol** | any alcohol sighting | 6 h | speak once, then dashboard | Zhao 2023 (A) |
| **Nicotine** | vape/cigarette in frame | 2 h | speak | Jha 2013 (A) |
| **Light** | bright minutes < 15 by 13:00; no daylight by 15:00; night light > 10 lx after 22:00 | 3 h | speak | Windred 2024 · Brown 2022 (A) |
| **Screens** | phone in frame in a dark room after 22:00 ≥ 20 min; screen block ≥ 90 min without a break | 90 min | speak | Brown 2022 (B) |
| **Body** | seated ≥ 90 min with < 2 min walking; gym or stairs in view with strength minutes < 30 this week; a hill/stairs available and VILPA < 3 min today | 90 min | speak | Momma 2022 · Stamatakis 2022 (A) |
| **People** | no conversation ≥ 10 min by 16:00; 0 distinct people by 19:00 | daily | speak once | Holt-Lunstad 2010 (A) |
| **Outside** | nature minutes projected < 120 by Thursday; PM2.5 > 35 while exercising outdoors; UV index ≥ 8 with skin exposed > 30 min | 4 h | speak for air/UV, dashboard for nature | WHO 2021 · White 2019 (A/B) |
| **Noise** | ≥ 70 dB(A) for ≥ 30 min; ≥ 45 dB(A) in the sleep window | 2 h | speak | WHO 2018 (B) |
| **Space** | cluttered desk/floor in the working frame; room dark during the day; bed unmade at 12:00 (opt-in) | daily | annotate only (grade C) | small studies; never spoken |
| **Sleep** | 60 min before habitual bedtime; bedtime drift > 45 min projected | daily | speak (the one allowed quiet-hours line) | Windred 2024 (A) |
| **Mind** | PVT stale > 24 h; reaction time +1 SD after a short night | daily | dashboard | Basner 2016 · Hagger-Johnson 2014 (A) |
| **Recovery** | sauna/gym in view with sessions < 2 this week; HRV < baseline − 1 SD for 3 days (athlete: allostatic flag) | daily | dashboard; speak for the allostatic flag | Laukkanen 2015 (B) |
| **Work** | ≥ 10 h at a screen in a day; projected > 55 h/week | daily | dashboard | WHO/ILO 2021 (A) |
| **Stress** | HR > resting + 35 bpm while seated ≥ 5 min (WHOOP) with no exercise | 60 min | speak | Balban 2023 (B) |

## Copy — three variants each, pick by rotation; `{}` are engine values

**Fuel**
- “That’s the second bowl of {item} today. Save the third — something with fibre gets tonight’s glucose flat.”
- “{item} again. Fine once. Add fruit or nuts next time and the pattern score holds.”
- “No vegetables in frame yet today. One handful with dinner keeps the week on pattern.”

**Caffeine**
- “Coffee at {time}. That’s inside your {cutoff} h cutoff — about an hour off tonight’s sleep.”
- “Second coffee after {cutoff_time}. Switch to decaf; the clock’s the thing you’re protecting.”
- “{time} caffeine. Expect a lighter night; nothing to do now but bed on time.”

**Alcohol**
- “That’s a drink. Water with it gets most of tonight’s HRV back.”
- “Drink {n}. Tonight’s recovery will read low; it’s the alcohol, not you.”

**Nicotine**
- “Nicotine in frame. Nothing else on this list is close to this one.”

**Light**
- “{minutes} bright minutes so far. Fifteen outside before {time} anchors tonight.”
- “No daylight yet today. A window won’t do it — step out for ten.”
- “Room’s at {lux} lux after 22:00. Dim it or your clock drifts {drift} minutes.”

**Screens**
- “Phone in the dark, {minutes} minutes. Screen off and the clock stops drifting.”
- “{minutes} minutes at the screen without a break. Two minutes standing resets it.”

**Body**
- “Seated {minutes} minutes. Two minutes of stairs counts as a hard-effort burst.”
- “Gym’s on your way home. Thirty minutes gets the week’s strength minutes done.”
- “That hill: one minute hard up it is a third of today’s bursts.”

**People**
- “No real conversation yet today. One call before {time} counts.”
- “You’ve been alone since {time}. Lunch with someone is the biggest single lever you have.”

**Outside**
- “Air’s at {pm25} µg/m³ right now. Run indoors, or at 07:00 tomorrow when it’s {pm25_tomorrow}.”
- “UV {uv}, {minutes} minutes exposed. Shade or a hat from here.”
- “{nature_min} of 120 nature minutes this week. Thursday walk in the park closes it.”

**Noise**
- “{db} dB for {minutes} minutes. Earplugs or another room.”
- “Night noise above 45 dB. Window closed or white noise on.”

**Sleep**
- “Bed in an hour. Screens down now and you keep your regularity streak.”
- “You’re drifting {minutes} minutes later than your window. Tonight’s the night to hold it.”

**Stress**
- “Heart rate’s {hr} sitting still for {minutes} minutes. Five slow breaths, longer out than in.”

**Recovery (athlete)**
- “Three days under your HRV baseline with strain climbing. Tonight is recovery, not training.”

**Space (annotate only — shown on the dashboard, never spoken)**
- `Desk clutter in the working frame most of the afternoon.` (grade C, no claim)

## Held-back reasons (exact strings)
`nothing worth saying` · `you were mid-conversation` · `quiet hours` · `spoke in the last hour` · `you did it before I asked` · `not enough evidence to speak`

## Outcome detection
- Fuel/Caffeine/Alcohol/Nicotine: no further sighting within 3 h → `Did it`.
- Light/Outside/Body: outdoor block, stairs, or strain spike within 90 min → `Did it`.
- Screens/Sleep: screen leaves frame within 5 min; bedtime inside window → `Did it`.
- People: conversation ≥ 10 min within 3 h → `Did it`.
- Noise: dB drops below threshold within 10 min → `Did it`.
- Stress: HR returns under resting + 15 within 10 min → `Did it`.
