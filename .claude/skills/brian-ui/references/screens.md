# Screens — every surface, every panel, every string

Bindings reference the payload from `python3 brian_score.py --json` unless marked (client).

---

## 0. Header (all pages)

Black bar, 64 px. Left: **BRYAN** (800, 24 px, white, letter-spacing −0.02em). Center: tabs as pills — Today · Week · Evidence · Plan · How it's scored — active pill white on black, 44 px tall, `aria-current="page"`, URL-synced (`/`, `/week`, `/evidence`, `/plan`, `/how-its-scored`). Right: status dot (green "Live" when glasses streamed in the last 60 s; grey "Seeded" otherwise) + profile chip "Bryan · 20 · Average" opening the Profile sheet. Nothing else. Tick counts, model names, latencies → Pipeline drawer.

Mobile: wordmark + status dot + profile; tabs become a bottom bar with the same five items, icons + 12 px labels, 56 px tall, safe-area padded.

---

## 1. Today

### 1.1 Ledger card (5 cols)
- Eyebrow (14, muted): `Today`
- Hero (72, 800, tabular): `+1.8 h` in earn or `−0.6 h` in cost. `hours_today`.
- Sentence (16, text): `of healthy life earned today. Likely range +0.3 to +3.4 h.` / for negative: `of healthy life cost today. Likely range −1.9 to +0.8 h.` from `hours_ci`.
- Rule.
- Two currencies, side by side:
  - `Fully-lived hours` — `19.1 / 24` (40, 800) + caption `how well you lived today, not how long` + hover popover listing components: reaction time, self-check, WHOOP recovery, illness (`experience.components`).
  - `Future healthy years` — `+3.4` (40, 800) + caption `vs a typical 20-year-old · 0.7 to 6.1` (`currencies.future_healthy_years`, `_ci`).
- Footer caption (12, muted): `Healthspan score 79 / 100 · How the hours are computed →` (link).
- States: loading skeleton with the sentence `Scoring today…`; no data → voice.md empty string.

### 1.2 What only your glasses can see (7 cols) — the product
Panel title (24): `What only your glasses can see`. Subtitle (14, muted): `Five layers no wearable measures. All from the camera and the mic, on your phone.`

Five tiles in a 5-column grid on desktop (2+3 on tablet, single column mobile), each 16 px radius, white on grey, 44 px tappable, opens its detail sheet. Every tile: icon (20), title (16, 700), number (40, 800, tabular), reading (14), 7-day sparkline (96×24, ink line, no axes), status dot, provenance chip.

| Tile | Number | Reading | Detail sheet |
|---|---|---|---|
| **Your clock** (Clock icon, `--clock` accent) | predicted melatonin onset `23:40` | `35 min later than last week` (arrow ↑ if later) · `bright light before 09:00 tomorrow moves it earlier` | 24 h light-band timeline (bright/dim/dark), first & last bright light, phase drift chart 14 days, model note `Forger99 pacemaker model on your eye-level light` |
| **Light** (Sun) | `44 min` bright | `first light 08:41 · dark by 22:10 · 40 min of screens after 22:00` | minutes per band vs targets ≥ 250 lx day / ≤ 10 lx evening / ≤ 1 lx sleep; night-light lux; `Brown 2022` |
| **People** (Users) | `3` people | `41 min talking · you spoke 48 % of the time · 6 laughs` | anonymous dots per person sized by minutes, minutes/day 14-day bars, reciprocity gauge as a simple split bar, `never names, never records words` |
| **Outside** (Trees) | `35 / 120 min` nature this week | `air 31 µg/m³ moderate · 42 dB at night` | green-view % timeline, nature minutes vs 120, PM2.5 today vs WHO 5, night noise vs 45 dB, `White 2019 · WHO 2021` |
| **Mind** (Brain) | `+38 ms` reaction time vs baseline | `2 lapses · clarity 3 / 5 · take the test to update` | PVT history (mean RT, lapses, RT variability), self-check trend, `Basner 2016 · Hagger-Johnson 2014`; button `Take Test` → /pvt |

Status dot rule: green when at/above target, amber within 30 %, red below; grey when unmeasured (tile still renders with `Unmeasured today` and the reason: `no frames after 14:00`).

### 1.3 What Bryan said
Title: `What Bryan said`. Subtitle: `Every time it spoke in your ear today, what it saw, and what happened next. Held back {n_held}.`

Rows (not cards): time (14, tabular) · trigger frame thumbnail 56×40 · said (16, text, quoted with curly quotes) · outcome chip: `Did it` (earn-soft) / `Didn't` (muted) / `Pending` (surface-2) · evidence (12, muted).

Example rows:
- `16:02` · frame · `“Second coffee. That lands inside your cutoff — about an hour of sleep tonight.”` · Didn't · `Drake 2013`
- `18:32` · frame · `“Your heart rate’s been 96 sitting still for six minutes. Stand up, breathe out slowly.”` · Did it · `Balban 2023`
- `12:08` · frame · `“That’s a drink at lunch. Water with it gets most of tonight back.”` · Pending

Under the list, a muted line: `Held back 14: 9 nothing worth saying · 3 you were mid-conversation · 2 quiet hours.` Tap opens the held-back list in the Pipeline drawer.

Rules: max 6 rows on Today; `Full day` link. Never show model names, latencies, or confidence here.

### 1.4 What the glasses saw
Title: `What the glasses saw`. Subtitle: `Frames stay on your phone. Each one is tied to the number it moved.` Right: `Full Day →` (44 px pill).
Pins (merged episodes, ≤ 12): 288 × 160 image, time chip (black), `Earned` / `Cost` chip, caption 16 (what), 14 (effect from engine `pins[].effect`), 12 muted `Evidence grade A`. Horizontal scroll with visible scrollbar, snap, keyboard arrows. Empty: voice.md string.

### 1.5 Tonight (left) — with sliders
Title: `Tonight, if nothing changes`. Subtitle: `Habitual bedtime 23:00.`
Three tiles: `Sleep 6.3 h` · `HRV −12 %` · `Clock +10 min` (`forecast`). Sentence: `Because: coffee at 16:00 is inside your 9 h cutoff; 40 min of screens after 22:00; 2 drinks.` Then `Still fixable:` line from the largest recoverable driver.
Sliders (client → `/api/forecast`): `Screens after 22:00` 0–120 min · `Last caffeine` 12:00–22:00 · `Drinks` 0–5. Labels above, values live, 44 px thumbs, keyboard steps. Sentence updates: `With no screens after 22:00: 6.5 h, HRV −12 %, clock 0 min.`

### 1.6 Next best minutes (right)
Title: `Next best minutes`. Subtitle: `Ranked by healthy-life hours per minute — and how often you actually do it.`
Rows: action (16) · `costs no time` / `3 minutes` (14 muted) · `you do this 71 % of the time` (12 muted, from `levers_personalized[].p_adherence`) · `+0.55 h` (earn). Primary row has a `Place It` button (writes a calendar block; confirmation toast `Placed: Thu 16:00 · 45-min walk in Hermann Park`, undo 8 s).

### 1.7 By layer
Title: `By layer`. Subtitle: `What each layer earned or cost today.`
Nine rows (Clock, Light, People, Outside, Mind, Body, Sleep, Fuel, Recovery): icon · name (16, 700) · reading (14 muted, the same `note` string as the tile) · 0–100 track · score (16, 700, tabular) · hours (16, 700, earn/cost/muted `—`). Row tap → the layer's detail sheet. Provenance chip at the row end.

### 1.8 The numbers your wearable already knows (collapsed)
Title exactly that. Subtitle: `Steps, strain, sleep stages, resting heart rate. Useful, not new.` Collapsed by default; chevron 44 px. Inside: compact stat tiles for steps / strain / RHR / HRV / sleep stages / SpO2 / respiratory rate / skin temp with WHOOP provenance. Never above 1.2.

### 1.9 This week (left) · What moves you (right)
- This week: title `This week`, subtitle `Accrued so far, and where you land by Sunday at this pace.` Rows sorted by relative shortfall; behind rows red, on-track green; caption `13 min still needed by Sunday.` Bright light and Nature rows sit first when behind (they're glasses-measured).
- What moves you: title `What moves you`, subtitle `Estimated on your own days, not the population’s.` Rows: `Green-view minutes → next-night HRV` · CI bar centred on zero · `+0.4 % per 10 min · 30 days`. Under 14 days: greyed row + `Population estimate. Your own number appears at 14 days — 9 to go.`

### 1.10 Last seven days
Title `Last seven days`, subtitle `Healthy-life hours per day, with the WHOOP numbers behind them.`
Bar chart (earn/cost bars, 6 px radius, zero line). Narrator layer: `annotations[]` runs → soft `ReferenceArea` + curved arrow + sentence; contrasts → two-column callout: `3 late-caffeine days: 6.0 h sleep · recovery 39 · −0.5 h/day` vs `the other 4: 7.6 h · 83 · +0.4 h/day` with `Drake 2013` muted. Table below, tabular numbers, worst day bold, `Seen` column lists drivers in words.

### 1.11 Footer
`Hours are a day’s share of the life-expectancy change implied by published hazard ratios, shrunk by evidence grade. Measurement and planning, not diagnosis.` · `How the hours are computed →` · provenance legend: Glasses · WHOOP · Entered · Seeded · Imputed.

---

## 2. Week (`/week`)
- Top: `Week of 8 Sept` with prev/next; weekly ledger for all layers (targets adaptive: caption `target moved 45 → 30 min: you hit 30 on 11 of 14 days`).
- Middle: the seven-day chart + narrator, full width.
- Bottom: `Contrasts` — every computed contrast as a card pair; `Best day` / `Worst day` with their pins.

## 3. Evidence (`/evidence`)
- Hour-by-hour timeline 06:00–24:00, one row per merged episode: time span, frame, type chip (Outside · People · Fuel · Screen · Body · Recovery), what it moved, evidence grade.
- Filters as pills: All · Earned · Cost · People · Outside · Fuel · Screens. URL-synced.
- Bulk actions: `Delete frames before today`, `Export my data`. Confirmation modal for delete.

## 4. Plan (`/plan`)
- `Placed` list (calendar blocks with layers hit and hours), `Suggested` list (levers with Place It), `Done this week` with the frames that proved it.
- Calendar week view (read-only) with placed blocks in earn-soft.
- Adherence note per lever: `you do this 71 % of the time`.

## 5. How it's scored (`/how-its-scored`)
- Stepper (numbered, horizontal): the 9 pipeline steps from `--registry`.
- Factor list, grouped by layer: label · grade chip · shrink · mini dose→HR curve (160×60) with the reference dose marked · source (full) · provenance chip.
- `Limitations` verbatim from the registry.
- `Data sources` table: Glasses (camera 1 frame / 5 s, audio features only), WHOOP (fields), Phone (GPS, accelerometer), Public APIs (Open-Meteo, OpenAQ, OSM), Entered (labs, genome flags, PVT).

## 6. PVT (`/pvt`)
- Black screen, one instruction line, `Start` (44 px). 180 s. Stimulus: white counter at random 2–10 s. Tap anywhere. False start message `Too early.` Result: mean RT, lapses, `+38 ms vs your baseline`, three-tap check (energy / mood / clarity, 1–5 as five 44 px buttons each), `Done`.

## 7. Profile sheet
Sections: `You` (age, sex, height, habitual bedtime) · `Goal` (Average / Athlete / Shift work / Genetic risk — changes targets live, toast `Athlete: sleep target 8.5 h, strain no longer penalised`) · `Genome` (APOE4, Lp(a) high, CYP1A2 slow, HFE — toggles, each with one line on what it changes) · `Labs` (ApoB, Lp(a), hs-CRP, HbA1c, ferritin, vit D — number inputs, date) · `Yearly` (DunedinPACE upload) · `Bryan’s voice` (on/off, quiet hours, max per hour default 1, what it may mention: Fuel / Substances / Light / Screens / Movement / People / Outside / Noise / Space / Sleep / Mind) · `Privacy` (frames on-device only, delete all, export) · `Sources` (Glasses connected, WHOOP connected, backfill 30 days).

## 8. Pipeline drawer (dev, collapsed at page bottom)
Title `Pipeline`. Live ticks, capture stats, decision feed with model, latency, confidence, held-back list. This is the only place those words appear.
