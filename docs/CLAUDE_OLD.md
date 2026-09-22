# CLAUDE.md

## What this is

A longevity/health tracker built on Ray-Ban Meta glasses (HackRice, two-person team).
The camera captures what a wrist wearable cannot: what you ate, whether you got outside
before 10am, whether you spoke to anyone, how long you stared at a screen.

- [SPEC.md](SPEC.md) — processing architecture, tick schema, work split.
- [hardware_software.md](hardware_software.md) — glasses/iPhone/Mac integration.
  Hardware-validated; trust it.
- [FINDINGS.md](FINDINGS.md) — what measurement, not reasoning, has established.

## Status — the loop closes, the depth is thin

The spine runs on real hardware: glasses → phone → Mac → Gemini tags → tick → B's gate
and reasoner → ElevenLabs → back out the glasses speakers. Verified live 2026-09-12 at
0.60 packets/s over LAN, malformed 0, dropped 0, disconnects 0. 67 tests pass.

A2–A17 are built. What is genuinely rudimentary, in the order it would bite:

- **The corpus is 143 frames over ~10 minutes, indoors, at night.** `outdoor`,
  `daylight`, and every vegetation tag are untested against real data — exactly what
  the "outside before 10am" pitch rests on. A daylight take is the top task left.
- **AI coverage is the fragile number.** Flash-Lite clusters 800–1100 ms, so a 1 s
  budget cut through the middle of the latency distribution and coverage swung 25–100%
  between runs. 1.5 s ticks is the fix, not yet re-measured on venue Wi-Fi.
- **Ticks are 1.5 s, not the 1 Hz the spec assumes.** Settled with B, who owns the
  windows this shifts. Every §3/§8 debounce is now counted in 1.5 s units.
- **A18's phone half has never run.** `MacLink.playAudio` is written but has not
  played one byte of real ElevenLabs mp3 on the device.
- **Nothing reconnects.** Drop the Wi-Fi and someone taps "Connect to Mac" again.
- **Two speak paths coexist.** `backend/pipeline/capture/speak.py` (B's — ElevenLabs,
  wired into the demo) and [src/longevity/speak.py](src/longevity/speak.py) (A17 —
  text-only, standalone `t0` CLI only). Not a conflict; a duplication to collapse.
- **The Xcode project lives outside this repo.** [ios/xcode-project.patch](ios/xcode-project.patch)
  is a snapshot and goes stale silently. Regenerate it after any Xcode-side change.

## Spec reliability — check before building on any section

| Section | Status |
|---|---|
| §2 (T0 tick producer), §3 (trigger gate) | **Thought through. Authoritative.** |
| §7, §8 (metric sources, thresholds) | AI-generated, not fully reasoned. Grain of salt. |
| §9 (VLM field set) | Already changed — B widened the menus on `main`. |
| Everything else | Reasonable, not final. |

Don't treat §7–§9 metrics, numbers, or enums as settled. Flag before depending on them.

## My role

**Person A — capture and audio.** I own everything upstream of the tick plus the audio
return path: the iOS bridge (DAT session, camera stream, JPEG encode, phone sensors,
WebSocket), the three `CaptureSource` adapters, WebSocket ingest on the Mac, the frame
ring buffer and `GET /frames`, all `sensor` and `device` tick fields, the T0 VLM call,
tick assembly, and TTS delivery. Not mine: trigger gate, episode builder, T1 reasoner,
actions, scoring, seeded data, dashboard. The tick object (§12) is the seam.

§9 fields live in exactly one place — [src/longevity/ai_fields.py](src/longevity/ai_fields.py).
Nothing else may name one. Adding a boolean is two lines there; an enum, four.

## Architecture in one line

Glasses → iPhone (Swift, Meta DAT) → WebSocket over LAN → Mac (Python, all logic)
→ audio back to the phone → Bluetooth → glasses speakers.

**The phone is a dumb adapter; the Mac is the system.** The phone samples, encodes,
sends, and plays. No ticks, no VLM, no logic on iOS.

## Invariants — do not violate

1. **T0 never blocks.** Capture and pixel math are synchronous and bounded.
2. **Drop, never queue** — at every stage. A stale frame has negative value.
3. **The T0 VLM call is fired, not awaited.** On overrun the result is discarded and
   the tick written with the `ai` block *absent*, never stale. Budget tracks the tick
   interval: 1.0 s standalone, `tick_interval_s - 0.1` integrated. Tolerate gaps.
4. **Frames live only in a 90s RAM ring buffer** on the Mac, never written to disk
   except as escalation evidence. This is also the privacy answer.

## Hardware: validated, don't re-litigate

Confirmed on the real device pair (hardware_software.md §29): the app builds and signs
under an Apple Personal Team, registers through Meta AI Developer Mode, reaches a
`started` DAT session, streams live camera frames, and plays AVSpeechSynthesizer audio
out the Ray-Ban speakers *while streaming*. Base is Meta's `CameraAccess` sample from
`facebook/meta-wearables-dat-ios`. Things that cost time if forgotten:

- `Access Wi-Fi Information` and `Hotspot Configuration` were **removed** — a free
  Personal Team can't provision them. Don't add them back.
- Enabled instead: Background Modes → External accessory communication, plus
  `UISupportedExternalAccessoryProtocols = com.meta.ar.wearable`.
- `NSLocalNetworkUsageDescription` and `NSAllowsLocalNetworking` **are now in the
  Info.plist.** Without them `URLSessionWebSocketTask` fails silently, like a backend bug.
- Physical iPhone only; the simulator can't do DAT.
- DAT availability is transient. "Device unavailable" and CoreBluetooth `API MISUSE`
  logs don't mean the build is broken — power-cycling the glasses has fixed it.
- `AVAudioSession.setCategory` throws OSStatus **−50** when a session already exists.
  Ignore it and speak anyway; returning early silently kills every utterance. Cost an hour.

## Stack

iOS: Swift, `MWDATCore` + `MWDATCamera` via SPM, built and signed in Xcode
(`xcode-select -p` must be `/Applications/Xcode.app/Contents/Developer`).
Mac: Python 3.11, FastAPI + uvicorn, numpy, SQLite, httpx. Gemini Flash-Lite for T0
tags (pinned to `gemini-2.5-flash-lite` — see FINDINGS before touching), Claude for T1,
ElevenLabs for TTS, Next.js dashboard (Person B).
Mac-side code never imports Meta SDK types — `VideoFrame` and friends stay on iOS.

My task breakdown is [PERSON_A.md](PERSON_A.md).
