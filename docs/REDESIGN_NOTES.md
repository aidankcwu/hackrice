# Zeroist app redesign — working notes

Aidan is talking through a ground-up redesign of the phone app; Claude keeps these notes.
Reuse existing components where they fit (Theme tokens, ledger rows, Connect rows, the
web calendar/analysis views, WKWebView embed). Nothing here is built yet.

## What is wrong with the current app (Aidan, 2026-09-24)

- Four tabs (Today, Calendar, Analysis, Protocol) plus the Connect screen.
- Calendar and Analysis each carry a hamburger menu with a disorganized pile of pages:
  treatments and tests for peptides, protocol library, biomarkers, devices, plus basic
  things (settings for personality, sources, account) and a "what we don't claim" page
  that feels strange. The pages have content but not what the demo needs.
- The hamburger belongs to Calendar/Analysis, not to the app: its open/closed state is
  per tab (go to Calendar, the menu is still open; go to Analysis, it may not be). It
  should be a standalone thing that supersedes all tabs, or not exist.
- Not intuitive overall.
- Protocol does not really do anything and is not a great feature.
- Things from the original website UI never made it in, e.g. a **session summary**.

## Core features (Aidan, round 2)

### Protocol (keep, make it real)
- The user has a protocol they want to stick to. The camera sees when they do a step and
  marks it on the protocol.
- Must be customizable and editable: start from a template (Lukas's library page has
  templates) or build your own. Track whether the protocol was followed fully or not.
- The glasses are not on for every step, so afterwards the user can fill in what they
  actually did with a plain checkbox per step.

What the backend does today (checked 2026-09-24): items have a name, one of four kinds
(dose, meal, winddown, walk), a daily HH:MM window and weekdays. While watching, a
`medication_seen` / `food_in_frame` / `outdoor_sustained` / `screen_sustained` escalation
inside the window marks the item `seen` with an evidence frame. A dose window that closes
unseen becomes `missed` and Bryan says one line. The wearer can mark done / undo by hand.
So the checkbox-after-the-fact already exists in the data model; the four fixed kinds and
the "one window per item" shape are the limits on customization. Web `library/` has
templates grouped into doses, meals, movement, light, sleep, screens.

### Home = Today
- A user intuitively treats Today as home. Right now it is one healthspan number with no
  explanation, then a raw log of everything the glasses saw. Not enough.

### Perception (side question, answered)
- The hosted backend is NOT using the MobileCLIP watcher: the Docker image has no
  torch/open_clip, so it logs "watcher unavailable, running without it (labeler on every
  tick)". It is Gemini Flash-Lite on every 1.5 s tick, exactly the old design. DECIDER=clerk.
- Watcher status (docs/PERCEPTION.md "Status" / "Known gaps", checked 2026-09-24): code
  complete and unit-tested (13 test files, 6.9 ms/frame on the Mac GPU), but never
  calibrated (thresholds are 0.60/0.40 placeholders, `corpus/` is empty), never run
  end-to-end on the glasses, never A/B'd against Gemini-every-tick, gate still reads
  Gemini only (`GATE_READS_WATCH=0`), and `deploy/Dockerfile` does `uv sync` without
  `--extra watcher`, so the container cannot load it. Phone also still sends 1 frame /
  1.5 s; the watcher was designed for the 2 fps stream.

### Home page contents (Aidan, round 3)
1. **Metrics area first.** Healthy-life hours is the main score. (Aidan doubts how it is
   measured; see "How hours are computed" below.) Sleep and wearable data (Fitbit,
   Apple Watch) belong here eventually but NOT in the demo build. Show daylight hours
   and true screen time (screens seen by the camera, not phone screen time).
   Known flaw, not for now: glasses are not worn all day; taken off facing a window
   they would "see" a window for hours. UI only for now, keep in mind.
2. **Below the metrics, what the glasses captured:**
   - A running daily summary: at 6 pm, a summary of the day so far (auto, or a
     "generate" button).
   - A stats sheet of today: groups of things done / things being tracked, a list of
     basic stats. The "wow, it tracked that" material: things nobody tracks by hand.
   - A list of past sessions to look back on. Maybe belongs on the Calendar (per-day
     summary) rather than home. Undecided.
3. **A morning blurb**, like Google Health's: a short AI paragraph at the start of the day
   (their example: low readiness → skip vigorous exercise, bedtime note, hot day → stay
   indoors). Ours would be built on what the glasses noticed.
4. **Glasses battery** up top, near the glasses connection control. (Feasible: DAT
   DeviceState exposes batteryLevel %, chargingState, donState worn/not worn, hingeState,
   thermalLevel, with no session needed. donState also answers "glasses off but streaming".)

How hours are computed (backend/pipeline/scoring/brian_score.py + healthspan.py): a
hazard-ratio engine. Each factor (steps, daylight, meals, caffeine timing, sleep, social
time, ...) has a dose-response curve from a named cohort study (e.g. Paluch 2022 Lancet
steps meta-analysis); today's dose → hazard ratio → hours of healthy life gained or lost.
Each observation carries provenance (live / seeded / derived / missing); a missing
factor is imputed at the population reference so it earns nothing. Not made up, but
most inputs in the demo are seeded, so the number is mostly the seed.

### Home page layout, top to bottom (Aidan, round 4)
1. **Key metrics** (Fitbit / WHOOP / Oura style). Primary: healthy-life hours, tap for a
   clear explanation of how it was measured.
2. **Daily summary + suggestions for the day**, personalised from the person's details
   (Google Health style).
3. **Sessions** (hackathon concept): a session log; each session shows what happened
   during it and a summary.
4. **Smaller stats for the day**: first sunlight, eating window, last caffeine, time
   socializing, true screen time, longest unbroken focus streak, hydration sightings.
5. **MAYBE the raw running log** of every notable event (today's ledger), collapsible,
   at the bottom, very short header. Exists to look cool.

Claude's proposed amendments (2026-09-24, pending Aidan's yes/no):
- A status row above the metrics: glasses (worn / battery), watching or not, Start/Stop.
- "Watched today: 3 h 10 min" beside the metrics so empty stats read as unwatched.
- Protocol card between summary and stats ("3 of 5 · Evening dose open until 10 pm").
- Sessions as one row that expands, not a list; each session opens its own summary.
- A designed empty state for a fresh install (nothing watched yet).
- Summary and suggestions as one card; date + "as of 6:12 pm" on it.
- Ledger at the bottom coalesced (no duplicate rows), header "Log".

### Locked (Aidan, round 5)
- Protocol folds into Home as a card: high-level list of today's steps, fulfilled or
  not. Tapping opens the full Protocol page (edit, templates, mark done, undo).
- A **fixed header**, not page content, visible while scrolling: status pill with
  battery · Start/Stop button · one glasses symbol (lit = worn/on, grey = off) · a
  "See preview" button that opens a window showing the live glasses camera view.
- Claude's amendments accepted: watched-today line, sessions as one expandable row,
  one summary card, coalesced log, designed empty state.

## Direction

- Model on WHOOP, Fitbit / Google Health, and similar health apps, but much simpler.
- Redesign from the ground up, reusing what exists.

## Decisions so far

(none locked yet; Aidan is still talking)

## Open questions to settle as we go

- Which tabs survive, and what is the home screen?
- What does a "session" mean (one Start→Stop of watching? a day?) and what the summary shows.
- What happens to Protocol: cut, fold into another screen, or keep as a small section.
- Where Connect lives once the app is redesigned (first launch + a status pill, as now?).
- Which of the web pages (calendar, analysis) stay, and whether they keep any menu.
- Settings: what a tester actually needs (invite link, voice on/off, permissions) vs. what goes.
