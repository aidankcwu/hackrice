# XCODE.md — Person A: get the glasses streaming to the Mac (A12–A14)

Everything on the Mac side is done and running. This is the phone side, in order.
Budget: 20–40 min, mostly signing and permission prompts. Details and exact code
snippets are in `ios/INTEGRATION.md`; this page is the checklist.

## 0. Pull

```bash
cd ~/hackrice && git pull origin main
```

You need three files from `ios/`: `CapturePacketSender.swift`, `PhoneSensors.swift`,
`INTEGRATION.md`. Do **not** start from a clean clone of Meta's sample — use the
project at `~/meta-wearables-dat-ios` as-is (it already has the local-networking
plist key, the frame hook in `CameraViewModel.swift`, and the entitlements a free
Personal Team can sign).

## 1. Add the two files to the CameraAccess target

Drag `ios/CapturePacketSender.swift` and `ios/PhoneSensors.swift` into
`samples/CameraAccess/` in Xcode, target membership = CameraAccess.

## 2. Two edits in `MacLink.swift` (yours; we did not touch it)

1. Path: `ws://\(host):\(port)/` → `ws://\(host):\(port)/ws/glasses`
2. Add `sendRaw(_:completion:)` next to `send(_:)` — exact snippet in
   `ios/INTEGRATION.md` §2b. Your existing `send(_:)` wraps everything as an
   `echo` message, which the Mac's ingest silently ignores; capture packets must
   go out raw.

## 3. Info.plist keys

Already present: `NSLocalNetworkUsageDescription`, `NSAppTransportSecurity →
NSAllowsLocalNetworking`, `NSCameraUsageDescription`.
Add: `NSMotionUsageDescription`, `NSLocationWhenInUseUsageDescription`.

## 4. Wire the sender (≈ 8 lines, in `CameraViewModel.swift`)

- Create `PhoneSensors()` and `CapturePacketSender()`; call `sensors.start()` and
  `sender.start(send: link.sendRaw)` once the socket reports connected.
- Where the DAT frame callback delivers a `VideoFrame`: `sender.offer(frame.makeUIImage())`.
- Set `sender.isConnected` from MacLink's status.
- Host/port for Rishi's laptop: `MacLink(host: "10.135.100.6", port: 8010)`.
  (Port 8010, not 8000 — 8000 is taken on that Mac, and 8010 is the integrated
  process that runs your T0 inside Person B's app.)

## 5. Build to the phone, tap Allow (camera, motion, location)

Keep the app foregrounded. `StreamConfiguration` stays at 2 fps; the sender
samples one frame every **1.5 s** (the agreed tick interval) and drops the rest.

## 6. Verify from the Mac (Rishi runs this side)

```bash
cd ~/hackrice/backend && uv run python -m pipeline.main --source glasses --vlm gemini --reasoner openai --port 8010
curl localhost:8010/ingest/stats      # received climbs ~0.67/s, malformed stays 0
curl localhost:8010/api/status        # capture.loop shows ai=..% (Gemini coverage)
```

Dashboard at `http://localhost:3000`: tick strip fills with tagged cells, the
Capture panel shows phone connected. If `received` stays 0: the socket path is
still `/`, or packets are going out as `echo` (step 2).

## 7. Back up the Swift (the repo is the only copy)

```bash
cd ~/meta-wearables-dat-ios
git add -N samples/CameraAccess/*.swift
git diff HEAD -- samples/ > ~/hackrice/ios/xcode-project.patch
git reset -q -- samples/
cd ~/hackrice && git add ios/xcode-project.patch && git commit -m "ios: xcode patch" && git push
```

## Trap you already hit

`AVAudioSession.setCategory` throws OSStatus −50 when a session already exists.
Ignore it and speak anyway; returning early silently kills every utterance.
