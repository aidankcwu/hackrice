# A12–A14 integration

Add `CapturePacketSender.swift` + `PhoneSensors.swift` to the `CameraAccess` target.
Nothing existing changes except two bits of `MacLink.swift`, quoted below.

## 1 · Info.plist — four keys, or it fails silently

| Key | Value |
|---|---|
| `NSLocalNetworkUsageDescription` | `Sends captured frames to the paired Mac on your local network.` |
| `NSAppTransportSecurity` → `NSAllowsLocalNetworking` | `YES` (already in the project) |
| `NSMotionUsageDescription` | `Uses motion to tell stillness from activity.` |
| `NSLocationWhenInUseUsageDescription` | `Uses your speed to tell walking from sitting.` |

First two are A11's — without them the socket dies with no error and reads like a Python
bug. Without the last two, `accel`/`gps_speed` are null forever, silently.

## 2 · MacLink.swift — two changes

**(a) The path.** `/ws/glasses` is hard-coded in `ingest.INGEST_PATH`; a bare `/` 404s
at the handshake. Change exactly this line in `init`, then dial
`MacLink(host: "<mac-lan-ip>", port: 8010)` — never `localhost`, which is the phone.

```swift
    url = URL(string: "ws://\(host):\(port)/")!            // before
    url = URL(string: "ws://\(host):\(port)/ws/glasses")!  // after
```

**(b) A raw send *that reports completion*.** `send(_:)` wraps its argument in
`{"type":"echo","text":…}`, so a capture packet would arrive as an echo carrying JSON as
a string and never decode. Add this beside it — A11's `send` stays as it is. The
completion is the sender's only delivery proof (`sentCount` moves when it fires with
`nil`), so forward it; swallowing it means reporting sends the Mac never got.

```swift
  /// A14: a pre-built `wire` message, with no envelope of its own.
  func sendRaw(_ text: String, completion: @escaping @Sendable (Error?) -> Void) {
    guard let task else {
      completion(URLError(.notConnectedToInternet))
      return
    }
    task.send(.string(text)) { error in completion(error) }
  }
```

## 3 · Six lines at the DAT frame callback

`StreamConfiguration` stays at **2 fps** (valid 2/7/15/24/30): the phone samples one
frame per 1.5 s and drops the rest; more fps only costs glasses battery.

```swift
@State private var sender = CapturePacketSender()          // owns a PhoneSensors

link.connect()
sender.isConnected = link.connected                        // set BEFORE start()
sender.start { json, done in link.sendRaw(json, completion: done) }

stream.videoFramePublisher.listen { frame in
    guard let image = frame.makeUIImage() else { return }
    sender.offer(image, at: Date())   // <-- the new line; thread-safe, throttles itself
}
```

Keep `isConnected` current or capture stops at the first drop and never resumes:
`.onChange(of: link.connected) { _, up in sender.isConnected = up }` (or
`sender.apply(macLinkStatus:)` off `link.status`).

`statusLine` reads `sent N · failed N · dropped N · 53 KB · 0.4s ago`, where **`sent` is
confirmed deliveries only**: `failed` climbing with `sent` stuck = dead socket, `dropped`
climbing alone = `isConnected` is false. Capture `link` strongly — a weak one that went
nil would drop `done` on the floor, and MacLink holds no reference back.

## 4 · Verify from the Mac

`curl localhost:8010/ingest/stats` → `connected: 1`, `received` rising, `malformed: 0`.
`malformed` rising instead of `received` means step 2(b) was skipped.

## 5 · From Person A's session

- **Work in the existing project**, `~/meta-wearables-dat-ios` (`samples/CameraAccess`) —
  not a clean clone of Meta's sample. It already carries
  `NSAppTransportSecurity → NSAllowsLocalNetworking`, the frame hook in
  `CameraViewModel.swift`, and the **removed** Wi-Fi / Hotspot entitlements that a free
  Personal Team cannot sign. A fresh clone loses all three.
- **Ports.** Standalone T0 is `ws://<mac-ip>:8000/ws/glasses` (`uv run t0 --source
  glasses`), verified with `curl localhost:8000/ingest/stats` — `received` climbs,
  `malformed` stays 0. The **integrated** process (Person B's app running T0 inside it)
  is `ws://<mac-ip>:8010/ws/glasses`, verified with `curl localhost:8010/ingest/stats`
  plus `curl localhost:8010/api/status` (look for the `capture.loop` line). Same packet
  either way. On Rishi's laptop the Mac's other service already holds 8000, so **8010 is
  the one to use there**.
- **Cadence is 1.5 s** (`TICK_INTERVAL_S`), not 1 Hz. `CapturePacketSender` defaults to
  1.5 and the integrated process tells the Mac's glasses source 1.5 too.
- **`CorpusRecorder.encode()` already does the exact 512 px / q70 encode** and the
  sender mirrors it byte for byte. The signatures match
  (`nonisolated static func encode(_:maxEdge:quality:) -> Data?`), so to keep one
  encoder in the target delete `CapturePacketSender.encode` and change the single call
  in `capturePacketJSON` to `CorpusRecorder.encode(image)`.
- **Regenerate the Xcode patch when it works**, exactly:
  ```
  cd ~/meta-wearables-dat-ios
  git add -N samples/CameraAccess/*.swift
  git diff HEAD -- samples/ > ~/hackrice/ios/xcode-project.patch
  git reset -q -- samples/
  ```
  then commit it — it is the only backup of the Swift.
- **Trap:** `AVAudioSession.setCategory` throws OSStatus **−50** when a session already
  exists. Ignore it and speak anyway; returning early there silently kills every
  utterance. `MacLink.speak` already does this — don't "fix" it.

## 6 Audio playback (A18)

`MacLink.swift` does not yet handle inbound `audio` messages. Add an
`AVAudioPlayer` property and route `audio` from `handle` with this code:

```swift
@ObservationIgnored private var player: AVAudioPlayer?

private func playAudio(_ obj: [String: Any]) {
  guard obj["format"] as? String == "mp3",
    let encoded = obj["data"] as? String,
    let data = Data(base64Encoded: encoded)
  else { return }
  do {
    let session = AVAudioSession.sharedInstance()
    try session.setCategory(.playback, options: [.allowBluetoothA2DP])
    try session.setActive(true)
  } catch {
    print("audio session (non-fatal): \(error)")
  }
  do {
    player = try AVAudioPlayer(data: data)
    player?.prepareToPlay()
    player?.play()
  } catch { print("audio playback failed: \(error)") }
}

// In handle(_:), after decoding obj:
if type == "audio" { playAudio(obj) }
```
