# Connecting a real wearable

The demo runs on a seeded wearable day (SPEC §14.2, `seed/biometrics.py`) written
with `origin='seed'`. Anything pushed into the ingest endpoints is written with
`origin='live'` and **wins for any window it covers**, so a real device can be
plugged in mid-run and the gate, the envelope and the dashboard switch over with
no code change. Unplug it and the seeded day is there again.

## Endpoints

| Route | Body |
|---|---|
| `POST /api/wearables/ingest` | canonical: `{"device": "apple_watch", "samples": [{"t": 1757700000, "metric": "heart_rate", "value": 72, "unit": "bpm"}]}` |
| `POST /api/wearables/ingest/health-auto-export` | Health Auto Export's own REST JSON |
| `POST /api/wearables/ingest/whoop` | one WHOOP v2 object, or a `{"records": [...]}` page |
| `GET /api/wearables/status` | what is stored, plus `live_connected` |
| `GET /api/biometrics?metrics=a,b,c` | several series at once, each tagged `seed` or `live` |

All three ingest routes return `{accepted, rejected, reasons}` and never fail a
whole batch on one bad sample. A sample is rejected when its metric is not in
`pipeline/wearables/__init__.py::LIVE_METRICS`, its device is not one of
`apple_watch | whoop | oura | sim`, its value is not numeric, or its `t` is more
than 48 h from now. Writes are idempotent on `(t, metric)`.

If `WEARABLE_INGEST_TOKEN` is set in the environment, every ingest request must
carry `X-Ingest-Token: <that value>`. Set it before exposing the port to a LAN.

## (a) Apple Watch, fastest path — Health Auto Export

No app to ship. Roughly ten minutes.

1. Install **Health Auto Export – JSON+CSV** on the iPhone paired to the watch
   and grant it Health read access.
2. Automations → new automation → **REST API**, POST, JSON, URL
   `http://<laptop-ip>:8010/api/wearables/ingest/health-auto-export`, header
   `X-Ingest-Token: <token>`.
3. Interval every 1–5 min, aggregation **none** (or `Avg` — the adapter reads
   `qty`, falling back to `Avg`).
4. Select these metrics: `heart_rate`, `heart_rate_variability`,
   `blood_oxygen_saturation`, `respiratory_rate`,
   `apple_sleeping_wrist_temperature`, `step_count`, `active_energy`,
   `walking_heart_rate_average`, `environmental_audio_exposure`. The adapter maps
   each onto this backend's metric names and ignores anything else in the body.
5. Phone and laptop on the same network; `uvicorn --host 0.0.0.0`.

Dates arrive as `"2026-09-12 14:02:00 -0500"` and are parsed **with** their
offset, so a phone in another timezone still lands on the right second.

## (b) Apple Watch, proper path — HealthKit in the iOS bridge

Person A's iOS bridge already runs next to the glasses, so it can carry the
watch too, and it gets samples in seconds rather than minutes.

- `HKAnchoredObjectQuery` per type (`heartRate`, `heartRateVariabilitySDNN`,
  `oxygenSaturation`, `respiratoryRate`, `stepCount`, `activeEnergyBurned`),
  persisting the anchor so a relaunch does not re-send the day.
- `HKObserverQuery` + `enableBackgroundDelivery` to wake on new samples.
- Convert each `HKQuantitySample` to `{"t": startDate.timeIntervalSince1970,
  "metric": ..., "value": ..., "unit": ...}` and POST the canonical payload.
- **A watchOS workout session is required for 1 Hz heart rate.** Without one the
  watch writes HR every few minutes and nothing on the phone can change that.
- `heartRateVariabilitySDNN` is SDNN, not RMSSD. It is stored as `hrv_rmssd`
  and labelled HRV; the two correlate but are not interchangeable — do not
  compare the number against a WHOOP RMSSD baseline.
- `oxygenSaturation` is a fraction (0.97). Send it as a percentage (97) or the
  tile will read 1 %.

## (c) WHOOP

1. Create a developer app at `developer.whoop.com`, redirect URI pointing at
   your own OAuth handler.
2. Scopes: `read:recovery read:cycles read:sleep read:workout read:profile`.
3. Poll, then forward each object verbatim to `/api/wearables/ingest/whoop`:

   | Endpoint | Becomes |
   |---|---|
   | `GET /v2/recovery` | `hrv_rmssd`, `spo2`, `wrist_temp_dev`, plus a daily `resting_hr` row |
   | `GET /v2/cycle` | `strain`, and `heart_rate` (cycle average) at cycle end |
   | `GET /v2/activity/sleep` | `respiratory_rate` at wake |
   | `GET /v2/activity/workout` | `heart_rate` average at start, max at end |

   `score.skin_temp_celsius` is absolute; the adapter subtracts a fixed 33.0 °C
   wrist baseline to get a deviation, because WHOOP does not publish the
   wearer's own baseline. Treat that deviation as coarse.

   Resting HR goes into the daily `seeded` table (SPEC §14.2), not the intraday
   series — it is a once-a-night number and the scorer already reads it there.
   Those daily rows are written with `source: "whoop_live"`, never `"whoop"`:
   `"whoop"` is what the SPEC §6 demo seed writes, so only `whoop_live` reads
   as live (`LIVE_DAILY_SOURCES` in `backend/pipeline/scoring/scorer.py`, and
   the dashboard's `LIVE_SOURCES`). Intraday samples keep device `whoop`; their
   `origin='live'` already marks them real.

4. A `pipeline.wearables.whoop_sync` poller that does the OAuth refresh and the
   polling loop is **subtask S8b**; today a cron job with `curl` is enough.

## (d) What is and is not intraday

| Device | Intraday reality |
|---|---|
| **Apple Watch** | HR every few minutes at rest, **1 Hz only inside a workout session**. HRV a handful of times a day. SpO2 and respiratory rate background-sampled, mostly at night. Steps and active energy per minute. |
| **WHOOP** | **No raw HR stream in the API.** Only per-record summaries: cycle average/max, workout average/max, strain updated a few times an hour. Recovery and sleep are once a night. |
| **Oura** | Daytime HR every 5 min, SpO2 and temperature nightly. Ring data lands after sync, so expect minutes of lag. |

So the only source that can produce the second-by-second series the
`biometric_anomaly` trigger was designed for (SPEC §14.3) is an Apple Watch in an
active workout session. Everything else is a coarser series, which the gate
handles — it needs five samples spanning 80 % of the window — but with a longer
window than the 20 s `DEMO_MODE` uses.
