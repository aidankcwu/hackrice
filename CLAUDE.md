# CLAUDE.md

## What this is

A longevity/health tracker built on Ray-Ban Meta glasses (HackRice, two-person team).
The camera captures what a wrist wearable cannot: what you ate, whether you got outside
before 10am, whether you spoke to anyone, how long you stared at a screen.

- [SPEC.md](SPEC.md) — processing architecture, tick schema, work split.
- [hardware_software.md](hardware_software.md) — glasses/iPhone/Mac integration.
  Hardware-validated; trust it.

## Spec reliability — check before building on any section

| Section | Status |
|---|---|
| §2 (T0 tick producer), §3 (trigger gate) | **Thought through. Authoritative.** |
| §7, §8 (metric sources, thresholds) | AI-generated, not fully reasoned. Grain of salt. |
| §9 (VLM field set) | Same — expect the field list to change. |
| Everything else | Reasonable, not final. |

Don't treat §7–§9 metrics, numbers, or enums as settled. Flag before depending on them.

## My role

**Person A — capture and audio.** I own everything upstream of the tick plus the audio
return path: the iOS bridge (DAT session, camera stream, JPEG encode, phone sensors,
WebSocket), the three `CaptureSource` adapters, WebSocket ingest on the Mac, the frame
ring buffer and `GET /frames`, all `sensor` and `device` tick fields, the T0 VLM call,
tick assembly, and TTS delivery. Not mine: trigger gate, episode builder, T1 reasoner,
actions, scoring, seeded data, dashboard. The tick object (§12) is the seam.

**Current task: get the glasses emitting a valid tick at 1 Hz with correct fields.**

## Architecture in one line

Glasses → iPhone (Swift, Meta DAT) → WebSocket over LAN → Mac (Python, all logic)
→ audio back to the phone → Bluetooth → glasses speakers.

**The phone is a dumb adapter; the Mac is the system.** The phone samples, encodes,
sends, and plays. No ticks, no VLM, no logic on iOS.

## Invariants — do not violate

1. **T0 never blocks.** Capture and pixel math are synchronous and bounded.
2. **Drop, never queue** — at every stage. A stale frame has negative value.
3. **The T0 VLM call is fired, not awaited.** 1s budget; on overrun the result is
   discarded and the tick is written with the `ai` block *absent*, never stale.
   Expect 50–80% AI coverage, so consumers must tolerate gaps.
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
- Physical iPhone only; the simulator can't do DAT.
- DAT availability is transient. "Device unavailable" and CoreBluetooth `API MISUSE`
  logs don't mean the build is broken — power-cycling the glasses has fixed it.
- `ws://` to the Mac will need `NSLocalNetworkUsageDescription` and
  `NSAllowsLocalNetworking`. Neither is in the Info.plist yet.

## Stack

iOS: Swift, `MWDATCore` + `MWDATCamera` via SPM, built and signed in Xcode
(`xcode-select -p` must be `/Applications/Xcode.app/Contents/Developer`).
Mac: Python 3.11, FastAPI + uvicorn, numpy, SQLite, httpx. Gemini Flash-Lite for T0
tags, Claude for T1, ElevenLabs for TTS, Next.js dashboard (Person B).
Mac-side code never imports Meta SDK types — `VideoFrame` and friends stay on iOS.

My task breakdown is [PERSON_A.md](PERSON_A.md). The Xcode project lives outside this repo.
