# STATE.md — Job 0 recon (task 0.1)

## 1. No-key run command
`cd backend && uv run python -m pipeline.main --source sim --speed 3 --port 8010`
(`pipeline/main.py:77-96`; `--vlm fake --reasoner fake` also avoid keys).
`scripts/preflight.py --offline` SKIPs Gemini/OpenAI/ElevenLabs (`scripts/preflight.py:97-98`).
Confirmed live: `/health`=404 on :8010. Its only definition is the standalone `src/longevity/server/app.py:59`, which the backend never mounts (it takes just `ingest`/`frames` routers, `backend/pipeline/api/app.py:51-59`). Use `/api/status` (`backend/pipeline/api/routes.py:54-56`) → `"ok":true,"problems":[]`.

## 2. Test commands
- Root: `cd backend && PYTHONIOENCODING=utf-8 uv run --directory . pytest -q -p no:cacheprovider ../tests`
  (`pytest.ini:1-2`). **132 passed**, 0 failed.
- Backend: `cd backend && PYTHONIOENCODING=utf-8 uv run pytest -q -p no:cacheprovider`
  (`backend/pyproject.toml:36-38`). **1640 passed, 5 failed** — Windows-only, not bugs
  (no IANA tzdata / POSIX mode bits / `astimezone()` near-epoch on Windows):
  `test_fitbit.py::test_heart_converter_uses_profile_timezone`, `::test_sleep_ignores_nap_and_cardio_range_midpoint`,
  `test_fitbit.py::test_pkce_exchange_and_secure_store`,
  `test_conversation.py::test_a_line_said_minutes_ago_is_not_said_again`,
  `test_config.py::test_the_demo_preset_is_frozen`.
- Dashboard (in `dashboard/`): `npx vitest run` (`dashboard/package.json:10`) → **111 passed** (9 files), 0 failed;
  `npx tsc --noEmit` → 0 errors (exit 0). `npm run build` not run: it would clobber the live :3000 dev server's `.next`.

## 3. Wire contract (`src/longevity/wire.py`)
All messages: `v=PROTOCOL_VERSION(1,:22)` + `type`. phone→Mac: `CAPTURE`(:27)/
`capture_packet()`(:43-72): t,image(b64 jpeg),gps_speed,accel,accel_burst?.
`HELLO`(:28)/`hello_message()`(:187-202): device,caps,t. `PONG`(:29,no builder).
`ANSWER`(:30)/`answer_message()`(:161-184): question_id,text,heard,t. Mac→phone:
`SPEAK`(:33)/`speak_message()`(:75-79): text,urgency. `AUDIO`(:34)/`audio_message()`
(:82-91): format,data(b64). `ASK`(:35)/`ask_message()`(:132-158): question_id,
listen_s,answer_kind,text. Both: `PING`(:38,keepalive,no builder). `ECHO`(:40)/
`echo_message()`(:94-95): text. `decode()`(:98-108) never raises, `{}` on malformed input.

## 4. Fixture JSON (sim instance, up 68+ min)
In `ios/Brian/Fixtures/`, valid JSON (`json.tool` checked).
- `today_healthspan.json` (`/api/healthspan`): overall, layers, years_delta, years_ci,
  hours_today, hours_ci, factors, ledger, forecast, levers, insights, day, as_of_hh,
  engine, measured, levers_free/personalized, pins, pins_total, experience,
  currencies, week_table, annotations, narrator_prompts, driver_rules, observations,
  provenance, effects, profile, baseline_sleep_h, window, conventions.
- `today_episodes.json` (`/api/episodes`): list×38; id, kind, start_t, end_t,
  duration_s, dominant, tick_count, open, reported, label.
- `today_decisions.json` (`/api/decisions?limit=50`): list×50; id, t, trigger,
  trigger_tick_id, episode_id, interpretation, confidence, actions, spoke, dropped,
  drop_reason, latency_ms, model.
- `status.json` (`/api/status`): demo_mode, source, uptime_s, tick_count,
  ai_coverage, t1_busy, dropped_escalations, last_tick_t, reasoner_mode, speed,
  tick_interval_s, gate, questions, conversation, speech_spoken, health, session.

## 5. Wearables ingest — `POST /api/wearables/ingest`
Route `pipeline/api/routes.py:315-329`. Optional `X-Ingest-Token` header, enforced
only if env `WEARABLE_INGEST_TOKEN` set (`routes.py:301-312`). Body
(`pipeline/wearables/ingest.py:44-55`):
`{"device":"apple_watch","samples":[{"t":1757700000,"metric":"heart_rate","value":72,"unit":"bpm"}]}`
(a sample may override `device`). `Sample`: t,metric,value,unit="",device="sim"
(`pipeline/wearables/__init__.py:91-103`). `MAX_SAMPLES` cap at `ingest.py:66-68`.
Siblings `/ingest/health-auto-export`(routes.py:332-343), `/ingest/whoop`(:346+) → same `ingest_samples()` sink.

## 6. Trigger example — `caffeine_seen`
`pipeline/gate/triggers.py:1260`: `_flag_hits("caffeine_visible",10.0,sighting_hits,
max_age_ms)`, kind `"caffeine_sighting"`. Reads `caffeine_visible` (`pipeline/models.py:217`).
`sighting_hits=max(1,hits(2))` (`triggers.py:1235`): 2 hits/1Hz scaled by tick rate,
fixed 10s window (doesn't scale, `triggers.py:1232-1234`). Episode debounce:
`sighting_min_hits=2, sighting_window_s=10.0` (`pipeline/episodes/builder.py:29-30`),
applied since `caffeine_sighting` ∈ `_SIGHTINGS` (`builder.py:172`, selected
`builder.py:297-303`); it's a literal `EpisodeKind` (`pipeline/models.py:170`). Test:
`backend/tests/test_gate.py:123-145` (slow-cadence sighting) + `:148-163` (doesn't block others).

## 7. Evidence frames
`GET /api/evidence/{decision_id}` (`pipeline/api/routes.py:523-525`) →
`reasoner.evidence.list(decision_id)`. `GET /api/evidence/{decision_id}/{ref}`
(:528-533) → JPEG via `.evidence.get(...)`, 404 if missing (:532). Frames copied from
the 90s RAM ring buffer at escalation (`pipeline/db.py:8-10`: ticks store no pixels).

## 8. Speak path for a new feature
Use `pipeline/actions/speech.py:68-147` `SpeechLimiter`, not `capture/speak.py`
directly. Ctor `(min_gap_s, max_per_hour)` (:71-77). `.allow(t)` (:81-110) gates on
simulated time (never wall-clock, :13-14): `min_gap_s` (:91-98) + hourly cap via
`t-3600.0` window (:88,:99-106). `.speak(text,urgency,t)` (:126-134) fire-and-forget,
dispatches via `get_speak_fn()`/`set_speak_fn()` (:53-61), wired to ElevenLabs at
startup. `pipeline/capture/speak.py:242-279` `make_speak_fn()` is the actual
glasses-socket sender (mouth-busy bookkeeping :91-100) — no hourly cap itself.
Two speak paths still coexist: backend `backend/pipeline/capture/speak.py` (behind the §8 limiter) and standalone `src/longevity/speak.py`, used only by `src/longevity/main.py:34` and `src/longevity/server/app.py:25` (`docs/CLAUDE_OLD.md:33-35`).

## 9. `db.py` table + migration example
Tables = raw SQL in `SCHEMA` (`pipeline/db.py:41-54` = `ticks`), applied by
`Database.init_schema()` (:281-286: `executescript` then `self._migrate()`). New
columns need an additive step in `_migrate()` (:288-309), guarded by
`PRAGMA table_info(<table>)`: `biometric_series.origin` — `ADD COLUMN origin TEXT
NOT NULL DEFAULT 'seed'` only if absent (:296-304); same for `episodes.label` (:306-308).

## 10. Stale claims in old CLAUDE.md (`docs/CLAUDE_OLD.md`; still-true ones moved to Hardware gotchas)
- "Nothing reconnects" (`docs/CLAUDE_OLD.md:32`): fixed by merge d94e3a1. `ios/MacLink.swift` `fail()`→`scheduleReconnect()` (:363-371), backoff 1-2-4-8-10 s (:134,:386-406), only `disconnect()` stops it (:177-187), 10 s ping vs the Mac's 30 s idle close (:131,:416-442; `src/longevity/server/ingest.py:48`); hello re-sent on reconnect (`ios/CapturePacketSender.swift:158-163`).
- "67 tests pass" (`docs/CLAUDE_OLD.md:18`): now 132 root + 1640 backend (`docs/STATE.md:11,13`).
- "Person A / Person B" (`docs/CLAUDE_OLD.md:50-57`): replaced by orchestrator + subagents (`PLAN.md:14-31`, `CLAUDE.md:15-16`).
- VLM budget "`tick_interval_s - 0.1` integrated" (`docs/CLAUDE_OLD.md:76`): now the full interval (`backend/pipeline/capture/bridge.py:56,64`).

## Hardware gotchas
Still-true claims from `docs/CLAUDE_OLD.md`, audited against `brian-ios`; stale ones are in §10.
- Physical iPhone only for DAT (`docs/CLAUDE_OLD.md:94`, `hardware_software.md:183`); the simulator runs only `-demo` fixtures (`ios/Brian/project.yml:24-25,94`).
- DAT availability is transient: "Device unavailable" / CoreBluetooth `API MISUSE` ≠ broken build; power-cycle the glasses (`docs/CLAUDE_OLD.md:95-96`, `hardware_software.md:700,712,726`).
- `AVAudioSession.setCategory` throws `OSStatus -50` if a session exists: log, speak anyway, never return early (`docs/CLAUDE_OLD.md:97-98`; honored at `ios/MacLink.swift:530-537,563-574`).
- Never add `Access Wi-Fi Information` / `Hotspot Configuration`; a free Personal Team can't provision them (`docs/CLAUDE_OLD.md:88-89`; removed `ios/xcode-project.patch:230,232`; none in `ios/Brian/project.yml:66-68`).
- Keep `UIBackgroundModes` external-accessory + `UISupportedExternalAccessoryProtocols = com.meta.ar.wearable` (`docs/CLAUDE_OLD.md:90-91`; `ios/Brian/project.yml:41-42`).
- Keep `NSLocalNetworkUsageDescription` + `NSAllowsLocalNetworking`, or `ws://` fails silently like a backend bug (`docs/CLAUDE_OLD.md:92-93`; `ios/Brian/project.yml:50-53`).
- `xcode-select -p` must be `/Applications/Xcode.app/Contents/Developer` (`docs/CLAUDE_OLD.md:103`; checked by `setup.sh:28-32`).
- T0 VLM stays on `gemini-2.5-flash-lite`; `gemini-flash-lite-latest` 400s (`docs/CLAUDE_OLD.md:105`; `src/longevity/vlm.py:64`, `FINDINGS.md:5`).
- Flash-Lite lands ~800–1100 ms, so ticks are 1.5 s not 1 Hz (`docs/CLAUDE_OLD.md:25-29`; `backend/pipeline/config.py:332`, `FINDINGS.md:36,52`).
- Outdoor/daylight/vegetation T0 tags are unvalidated on real frames; the only corpus was 143 indoor night frames (`docs/CLAUDE_OLD.md:22-24`; `FINDINGS.md:102-103` indoor only; no daylight corpus in git). Demo default is `--source sim` (`backend/pipeline/main.py:77-78`).
- Mac-side Python never imports Meta SDK types (`docs/CLAUDE_OLD.md:107`; `git grep "VideoFrame\|MWDAT" -- '*.py'` = 0 hits).
- The running iOS app still lives outside the repo; `ios/xcode-project.patch` is its only backup and goes stale silently (`docs/CLAUDE_OLD.md:36-37`; `ios/README.md:3-5,45-51`). `ios/Brian/` has only `project.yml`, `Sources/` is empty.
- `MacLink.playAudio` (`ios/MacLink.swift:522`) has no recorded on-device run of real ElevenLabs mp3 (`docs/CLAUDE_OLD.md:30-31`; no hit in `FINDINGS.md` / `hardware_software.md`).
- Free Apple Personal Team provisioning expires after 7 days (`ios/README.md:66`; not in `docs/CLAUDE_OLD.md`).
