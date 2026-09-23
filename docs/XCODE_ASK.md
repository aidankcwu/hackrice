# XCODE_ASK.md — test ask/answer on the live glasses

For Aidan's Claude session. Work through this top to bottom; every step has a
pass condition. Stop and report at the first step that fails — do not improvise
around a failure, the triage table at the bottom says what each one means.

Branch: `ask-answer/batch-1`. Design: [docs/ASK_DESIGN.md](docs/ASK_DESIGN.md).
Phone integration notes: [ios/INTEGRATION.md](ios/INTEGRATION.md) §7–§8.

Budget: ~40 min. Needs the glasses, the phone, the Mac, all on one Wi-Fi.

---

## 0. Preconditions (Mac)

```bash
cd ~/hackrice && git fetch origin && git checkout ask-answer/batch-1 && git pull
cd backend && uv sync && uv run pytest -q          # expect 1259 passed
cd .. && uv run pytest -q tests                     # expect 107 passed, 1 skipped
cd backend && uv run python scripts/preflight.py   # all PASS; note the ws:// address it prints
```

Pass: both suites green, preflight prints `ws://<mac-ip>:8010/ws/glasses`.
If 8010 is "occupied by another service", something else is running — find it
with `lsof -i :8010` and stop it, or use `--port 8011` everywhere below.

## 1. Xcode — three files, one plist key

Project: `~/meta-wearables-dat-ios`, target `CameraAccess`. Do **not** start
from a clean clone (docs/XCODE.md §0 explains why).

1. Replace `samples/CameraAccess/MacLink.swift` with `ios/MacLink.swift` from the branch.
2. Replace `samples/CameraAccess/CapturePacketSender.swift` with `ios/CapturePacketSender.swift`
   (only `helloJSON()` changed: it now sends `"caps": ["ask"]`).
3. Add `ios/QuestionListener.swift` to the target (drag in, target membership = CameraAccess).
4. Info.plist: add `NSSpeechRecognitionUsageDescription` = `Transcribes short answers to questions asked through the glasses.`
   `NSMicrophoneUsageDescription` already exists. Both are required or iOS kills the app on the first ask.
5. In the camera view, next to the existing status text, add:
   ```swift
   Text(link.listener.statusLine).font(.caption)
   ```
6. Build to the physical iPhone (simulator cannot do DAT). Fix any compile error
   before going on — the files typecheck for iOS 17 on their own, so an error here
   is almost certainly a missing file or a stale copy of MacLink.

Pass: app installs and launches; camera preview and "Connect to Mac" work as before.

## 2. Bring the loop up

Terminal A (Mac):

```bash
cd ~/hackrice/backend && uv run python -m pipeline.main --source glasses --vlm gemini --reasoner openai --port 8010 --fresh
```

Phone: connect to the Mac (type the IP from preflight if it changed; Apply),
start the DAT session, start streaming.

```bash
curl -s localhost:8010/api/status | python3 -c "import json,sys; h=json.load(sys.stdin)['health']; print(h['phone']['connected'], h['phone']['caps'], h['problems'])"
```

Pass: prints `1 ['ask'] []`. If `caps` is `[]` the phone is running an old
build (step 1.2 not applied). If `problems` has `no_packets_10s`, the DAT
stream is not up yet — start it and re-check.

## 3. Test 1 — a forced question (no gate involved)

Wear the glasses. Terminal B:

```bash
curl -s -X POST localhost:8010/api/ask -H 'Content-Type: application/json' -d '{"text":"Can you hear me? Say yes or no."}'
```

Expected on the glasses: the sentence plays in the ElevenLabs voice, then a
short pause, then the phone's status line reads `answer listening…`. Say
**"yes"** clearly. Then:

```bash
sleep 6; curl -s localhost:8010/api/questions?limit=1 | python3 -m json.tool
```

Pass: `"status": "answered"`, `"answer_text"` contains your word, `"heard": true`,
`parsed.understood` true, `parsed.confirmed` true.

**Record these three numbers in FINDINGS.md** (they are the whole point of this test):

- **Switch latency**: seconds between the question ending and `answer listening…` appearing.
  Under 1.0 s is fine. Over 1.5 s: raise `listen_s`'s lead-in (the 300 ms beat in
  `MacLink.waitForPlayback`) and note it.
- **Input route**: the Xcode console line `listening inputs: [...]`. Must name the
  Ray-Ban route (port type `BluetoothHFP`), not `MicrophoneBuiltIn`.
- **Frames during the window**: run `curl -s localhost:8010/ingest/stats | grep received`
  before asking and again while listening. Note whether `received` kept climbing
  (DAT streamed through the hands-free switch) or paused, and for how long.

## 4. Test 2 — audio recovers

Immediately after Test 1:

```bash
curl -s -X POST localhost:8010/api/speak -H 'Content-Type: application/json' -d '{"text":"This should sound normal again."}'
```

Pass: plays at normal A2DP quality (not tinny/phone-call quality). If it sounds
like a phone call, the session did not restore — see triage.

## 5. Test 3 — the real trigger, with a follow-up

Put a can/bottle/glass of something alcoholic-looking in front of the glasses
for ~5 s (the gate needs two positive ticks inside 10 s in demo mode). Watch
Terminal A for `alcohol_seen`.

Expected: the reasoner asks something like "That yours?" in its own words.
Answer **"yes"** and nothing else. Expected next: a follow-up like "How many
today?" Answer **"two"**.

```bash
curl -s localhost:8010/api/questions?limit=3 | python3 -c "import json,sys; [print(q['status'], '|', q['question'], '|', q['parsed']) for q in json.load(sys.stdin)]"
curl -s localhost:8010/api/episodes | python3 -c "import json,sys; [print(e['kind'], e['reported']) for e in json.load(sys.stdin) if e.get('reported')]"
```

Pass: two rows, both `answered`; the follow-up's `parsed.count` is `2`; the
`alcohol_sighting` episode shows `reported.count == 2`. The dashboard decision
feed shows the `alcohol_seen` row with an `ask` action (`outcome: sent`) and an
`answer:q_…` row. If GPT chose not to ask, that is allowed — force one with
Test 1's curl and `"episode_id"` set to the sighting's id instead.

## 6. Test 4 — silence and the gap

Force a question (Test 1 curl) and **say nothing**. After ~25 s:

```bash
curl -s localhost:8010/api/questions?limit=1 | python3 -c "import json,sys; q=json.load(sys.stdin)[0]; print(q['status'], q['parsed'])"
curl -s localhost:8010/api/summary/today | python3 -c "import json,sys; print([l['line'] for l in json.load(sys.stdin)['lines'] if 'asked' in l['line']])"
```

Pass: `expired` and a memory line `asked: … — no answer`. Then immediately
force another question: pass is `"suppressed_reason": "ask_min_gap"` (30 s
rule) — nothing should play.

## 7. Test 5 — glasses off and on

Take the glasses off (or power-cycle them) while a question is open. Pass:
the phone status line returns to `answer idle`, no crash, and after the glasses
come back a new forced question works end to end.

## 8. Wrap up

1. Re-snapshot the Xcode patch (the only backup of the Swift):
   ```bash
   cd ~/meta-wearables-dat-ios
   git add -N samples/CameraAccess/*.swift
   git diff HEAD -- samples/ > ~/hackrice/ios/xcode-project.patch
   git reset -q -- samples/
   ```
2. Append a "## Ask/answer on hardware" section to `FINDINGS.md` with the three
   numbers from Test 3, plus pass/fail per test.
3. Commit both on the branch and push:
   ```bash
   cd ~/hackrice && git add ios/xcode-project.patch FINDINGS.md && git commit -m "ask/answer: hardware findings and Xcode patch" && git push
   ```

---

## Triage

| Symptom | Meaning | Fix |
|---|---|---|
| `/api/ask` returns `ask_unsupported` | phone hello has no `caps` | step 1.2 not applied, or old build still installed |
| `/api/ask` returns `no_transport` | no phone socket | tap Connect; check IP with preflight |
| Question plays but status never says listening | playback completion never fired, or permission denied | check Xcode console for `microphone or speech denied` / `timed out before listening`; Settings → app → allow mic + speech |
| `listening inputs:` shows `MicrophoneBuiltIn` | iOS did not route to the glasses' mic | glasses not the active audio device; pick Ray-Ban in Control Center, retry. If persistent, drop `.allowBluetooth` in `QuestionListener.startListening` to use the phone mic deliberately |
| Transcript empty, `heard: false` | recognizer heard nothing in the window | speak sooner/louder; if repeatable, raise `ask_listen_s` in `config.py` (demo timings) |
| Test 2 sounds like a phone call | session not restored to A2DP | look for `restore playback session (non-fatal)` in console; report the error text |
| `received` stops during the window and does not resume | DAT stream died on the HFP switch | report; fallback is the phone-mic change above |
| Answer arrives but row stays `open` | wire `answer` rejected | `curl localhost:8010/ingest/stats` — `malformed` climbing means a bad field (`heard` must be a bool) |
| Row `answered` but `parsed` is `{"understood": false, "note": "reasoner busy"}` | T1 slot was busy when the answer landed | expected under load; answer again after the current decision finishes |

Anything not in this table: capture the Xcode console from the ask to the
answer, `curl localhost:8010/api/status`, and the Terminal A log, and report
all three verbatim.
