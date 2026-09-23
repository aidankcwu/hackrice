# UI contract: what the phone app reads

[now] = the phone reads it today. [next] = the phone computes it from `/api/month` today; it switches to the backend's value once served.

## Conventions
- Base `NEXT_PUBLIC_API_BASE` (default `http://localhost:8010`). Header `Authorization: Bearer <NEXT_PUBLIC_API_TOKEN>` when set.
- Timeout 15 s. A failed read keeps the last good answer on screen.
- Errors: no answer → "Backend unreachable. Check that the Mac and phone share Wi‑Fi." · 401/403 → "Backend refused this phone. Check the API token." · other → "Backend error N. Restart the backend on the Mac." Button "Try again".
- Today reads: times in epoch seconds, backend clock. Month reads: minutes after local midnight, int; `sleep.bed` and `wakings[].start` negative = before midnight.
- Dates ISO `YYYY-MM-DD`, local. Percent 0–100, one decimal. Durations in minutes unless the name ends `_s`.
- Unknown = key absent or `null`, never `0`. `seeded: true` on anything not from a device.

## Screens
| Screen | Endpoint | Poll | Missing or failed |
|---|---|---|---|
| Today | `GET /api/status` [now] | 30 s while in front, and on return | status strip shows the error sentence |
| Today | `GET /api/healthspan` [now] | 30 s | hero not drawn |
| Today | `GET /api/episodes` [now] | 30 s | ledger from decisions only |
| Today | `GET /api/decisions?limit=50` [now] | 30 s | ledger from episodes only; both missing: no ledger |
| Today | `GET /api/month` [now], last day | once per open | ceilings and "Your day" not drawn |
| Calendar day, week | `GET /api/month` [now] | once per open | "The month appears once the backend serves it." · no answer: "The Mac is not answering. The calendar appears once it does." |
| Analysis | `GET /api/month` [now], last 7 or 14 days | once per open | "The analysis appears once the backend serves a month." |
| Protocol | `GET /api/protocol/today` [now] | 30 s | error sentence + "Try again" · `items: []`: "No items yet. Add the first dose window." |
| Protocol | `GET /api/month` [now], last day | once per open | "Daily amounts" not drawn |
| Thumbnails | `GET /api/evidence/{decision_id}/{frame_ref}` → `image/jpeg` [now] | on draw | no thumbnail |

## Today
```
status      source "glasses"|"sim"|"replay"|"webcam" · last_tick_t s · health.phone.connected int|null
            health.problems string[] ("no_packets_10s", "no_ticks_60s", …)
            session {id, name, started_t s, ended_t s|null, elapsed_s s} | null
healthspan  hours_today h, signed · years_delta years|null · overall 0–100
            provenance {key: {source "live"|"seeded"|"derived"|"missing", basis "glasses"|"whoop"|"apple_watch"|…}}
            factors[].provenance
episodes[]  id · kind · start_t s · label string|null
decisions[] id · t s · episode_id|null · dropped bool · spoke bool · interpretation · actions[{type, …}]
```
Missing: session `null` → "Not watching", button "Connect glasses" · `elapsed_s` absent → `last_tick_t − started_t` · `years_delta` null → shown as 0 · no live source → no provenance chip · `label` null → kind in words · no session, no episodes, no decisions → "Put the glasses on. Counting starts the moment the camera is up."

## Month  `GET /api/month?end=YYYY-MM-DD&days=30`  [now: no query, the 30 days ending today]
```jsonc
{ "city": "Houston", "lat": 29.7604, "lon": -95.3698, "utc_offset": -5, "seeded": true,
  "rules_version": "2026-09-22",                                            // [next]
  "days": [{
    "date": "2026-09-22",
    "until": 1215,                        // min; 1440 once the day is over
    "type": "late_caffeine",              // seeded label; only "sick" is read (relaxes the protocol)
    "sleep": { "bed": -88, "wake": 392, "minutes": 466, "deep": 104, "rem": 110, "fragmented": false,
               "wakings": [{ "start": 135, "minutes": 18, "baby": true }] },   // the night ending that morning
    "events": [{ "id": "2026-09-22-21", "kind": "caffeine", "start": 970, "drink": "coffee" }],
    "findings": [],                       // [next] Finding[]
    "ceilings": {},                       // [next] Ceilings
    "daily": { "water_ml": 2010, "people_min": 56, "daylight_min": 34 },      // [next]
    "aqi": 42, "weather": { "high_f": 91, "summary": "Clear" }, "held_back": 7  // not read
  }] }
```
Missing: `sleep` or `until` absent → required, the day cannot be drawn · no event of a kind → counted as not done (Light, Move, Peptide get a red X once the window closes) · `findings`, `ceilings`, `daily` absent → phone computes them.

## Events the calendar draws  (every event: `id` string, `kind`, `start` min)
| kind | fields | drawn as |
|---|---|---|
| caffeine | drink "coffee"\|"tea"\|"energy_drink" | tick in Caffeine lane ≤ 12:30 · red bar "Coffee 16:10" after |
| meal | label, food "whole"\|"fast_food"\|"sweets"\|"ultra_processed" | tick in Food lane, name not drawn · red bar "Dinner 20:40" after 18:30 |
| skipped_meal | meal "breakfast"\|"lunch"\|"dinner" | Food window grey, red X at 18:30 |
| workout | minutes, label, vigorous bool | tick in Move · red bar "Run 19:30" when vigorous and start ≥ 19:00 |
| outdoor | minutes, label, sunlight bool, uv_peak int [next] | tick + minutes in Light lane · red bar "Outside at UV 9, 14:00" when sunlight, ≥ 30 min, UV ≥ 8; `uv_peak` absent → clear-sky estimate from the sun's height |
| screen | minutes, device "phone"\|"computer" | red bar "Phone 21:45" when it runs past 21:30 |
| phone_in_bed | minutes | red bar "Phone in bed 22:50" |
| alcohol | drinks int, label | one red bar at the last drink, "Drinks 21:50" |
| nicotine | label | red bar "Nicotine 22:35" |
| nap | minutes | red bar "Nap 16:40" when start ≥ 16:00 |
| sauna, cold | minutes | tick in Move |
| peptide | dose "AM"\|"PM", taken bool, thumb string\|null | tick · window green when taken inside it · red X at the window's end when `taken: false`, or absent once it closes |
| sleep (`day.sleep`) | bed, wake, wakings[{start, minutes, baby}] | Sleep lane green when wake within 30 min of 6:30 · wakings grey ticks, never red · red bar "Bed 0:39" when bed after 23:00 |
| water | ml | Protocol "Daily amounts" only |
| conversation | minutes, label | Protocol "Daily amounts" only (People) |

Rules only, not drawn: `sedentary{minutes}`, `stress{scene, hr bpm, resting bpm}`, `supplements{label}`. Not read: `drive`, `work`, `mind_check`, `whispered`, `asked`, `acted`.

## Finding  [next]
```json
{ "rule": "caffeine", "event_id": "2026-09-22-21", "time": 970, "tone": "violation",
  "line": "About half of a 16:10 coffee is still in you at 22:30.", "cognition": 0.04, "body": 0.01 }
```
- `tone`: inside (green, scores 0) · violation (red) · watch (amber, scored) · neutral (grey, scores 0 except sleep_fragmented and sick).
- `cognition`, `body`: fraction lost at full strength, after scaling. `event_id` absent for day-level findings (sleep, targets).

## Ceilings  [next]
```json
{ "cognition": 85.9, "body": 91.8,
  "contributions": [{ "rule": "alcohol", "from": "2026-09-18", "event_id": "2026-09-18-30", "weight": 1.0, "cognition": 3.0, "body": 2.0 }],
  "top": ["Drinks at 19:50 yesterday", "Short sleep last night"],
  "lever": { "rule": "caffeine", "text": "Caffeine before 12:30" } }
```
- `cognition`, `body`: % of the wearer's ceiling that day. `contributions[]`: points lost that day, biggest first. `top`: the two biggest, in words. `lever`: the one change that lifts tomorrow most.
- Formula: 100 × Π over findings from the last 4 days of (1 − effect × scale × weight).
- weight: offset = day − finding's day − lands; offset < 0 → 0 · 0 → 1 · else max(0, 1 − offset / decay); decay 0 → 0.
- scale: alcohol × drinks · sleep_short × min(6, minutes short / 30) · workout ending after 16:30 × 0.5 · dose outside window × 0.5 · long nap inside window × 0.25 · else 1.
- No double count: a thin night after a red that works through sleep (late caffeine, drinks, phone in bed, screens after 21:30, late dinner, late workout, late nap) is neutral. Sick day: sleep findings and `sick` only; nothing red.

## Rules  [next: backend owns]   lands 0 = same day, 1 = next day · decay = days to fade after landing · effects to verify
| rule | window or target | cog | body | lands | decay |
|---|---|---|---|---|---|
| caffeine | 6:30–12:30 (10 h before bed) | .04 | .01 | 1 | 1 |
| movement | vigorous done by 16:30; red from 19:00 | .02 | .02 | 1 | 1 |
| last_meal | last meal by 18:30 | .02 | .02 | 1 | 1 |
| eating_window | first meal after 10:00 (amber) | 0 | .01 | 0 | 0 |
| food_quality | whole food (amber) | .015 | .005 | 0 | 0 |
| skipped_meal | a meal seen in the window | .05 | .02 | 0 | 0 |
| alcohol | none, per drink | .03 | .02 | 1 | 2 |
| nicotine | none | .01 | .02 | 0 | 1 |
| screens | off from 21:30 | .02 | 0 | 1 | 1 |
| phone_in_bed | none | .02 | 0 | 1 | 1 |
| nap | 13:00–15:00, ≤ 20 min; red from 16:00 | .02 | 0 | 1 | 1 |
| sedentary | stand every 90 min (amber) | 0 | .01 | 0 | 0 |
| sauna_cold | sauna 16:00–20:30, cold ≥ 2 h before bed (amber) | .005 | 0 | 1 | 1 |
| stress | seated HR < 1.4 × resting (amber) | .005 | 0 | 0 | 0 |
| air | outdoors at AQI < 100 (amber) | 0 | .005 | 0 | 0 |
| uv | < 30 min direct sun at UV ≥ 8 | .01 | .02 | 0 | 1 |
| peptide | AM 7:00–10:00, PM 19:00–22:00 | 0 | .015 | 0 | 1 |
| sleep_window | in bed by 22:30; red after 23:00 | 0 | 0 | 1 | 0 |
| sleep_short | ≥ 7 h | .015 | .01 | 0 | 1 |
| sleep_fragmented | unbroken (grey, scored) | .06 | .03 | 0 | 2 |
| sleep_deep | ≥ 75 min deep | .01 | .03 | 0 | 1 |
| wake_anchor | up within 30 min of 6:30 (amber) | .01 | 0 | 0 | 0 |
| morning_light | 10 min outside within 60 min of waking (amber) | .01 | 0 | 1 | 1 |
| daylight | 60 min in sun by sunset (amber) | .01 | .005 | 1 | 1 |
| people | 30 min face to face by 21:00 (amber) | .01 | 0 | 0 | 0 |
| water | 2 L by 18:00 (amber) | .01 | .01 | 0 | 0 |
| sick | recovery day (grey, scored) | .15 | .25 | 0 | 1 |

## Calendar day, Calendar week, Analysis  (drawn from the month; nothing extra to serve)
- Day: date, `ceilings.cognition`, `ceilings.body`; lanes Sleep 22:30–6:30 · Light 6:30–7:30 + 60 min by sunset · Caffeine 6:30–12:30 · Food 10:00–18:30 · Move by 16:30 · Screens off 21:30 · Peptide 7:00–10:00, 19:00–22:00.
- Green = rule met · grey = not met, or window not closed yet · red X = missed without a violation · red bar = violation. Sunset from `lat`, `lon`. Week: 7 columns, ceilings rounded above each.
- Analysis: 7 or 14 consecutive days, plus the night and day after each red: `sleep.deep`, `sleep.rem`, `sleep.bed`, next day's ceilings. Next night missing → "Tonight's sleep will show it." Nights with `fragmented: true` or before a sick day are left out of the figures.

## Protocol  `GET /api/protocol/today`  [now]
```jsonc
{ "day": "2026-09-22",
  "items": [{ "id": "p1", "name": "Peptide AM", "kind": "dose",          // dose|meal|winddown|walk
              "window_start": "07:00", "window_end": "10:00",             // local HH:MM, same day
              "days": [0, 1, 2, 3, 4, 5, 6],                              // 0 = Monday
              "status": "seen",                                           // waiting|seen|done|missed|undone
              "seen_t": 1789030920, "evidence_ref": "d42/f3",             // s|null · "<decision_id>/<frame_ref>"|null
              "created_t": 1789000000, "updated_t": 1789030920 }] }       // s · s|null
```
- Writes [now]: `POST /api/protocol` {name, kind, window_start, window_end, days?} → item · `POST /api/protocol/{id}/done` and `/undo` → item + `day` · `DELETE /api/protocol/{id}` → {id, removed}.
- Missing: `seen_t` null → no time · `evidence_ref` null → no thumbnail · failed write → row keeps its state, error sentence.
- Daily amounts [next: `days[-1].daily`]: water_ml by 18:00 (target 2000) · people_min face to face by 21:00 (target 30) · daylight_min in sun before sunset (target 60). Absent → phone sums `water`, `conversation`, `outdoor` events.
