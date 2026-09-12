# A12–A14 integration

Add `CapturePacketSender.swift` + `PhoneSensors.swift` to the `CameraAccess` target.
Nothing existing changes except two lines of `MacLink.swift`, quoted below.

## 1 · Info.plist — four keys, or it fails silently

| Key | Value |
|---|---|
| `NSLocalNetworkUsageDescription` | `Sends captured frames to the paired Mac on your local network.` |
| `NSAppTransportSecurity` → `NSAllowsLocalNetworking` | `YES` |
| `NSMotionUsageDescription` | `Uses motion to tell stillness from activity.` |
| `NSLocationWhenInUseUsageDescription` | `Uses your speed to tell walking from sitting.` |

First two are A11's: without them the socket dies with no error and reads like a Python
bug. Without the last two, `accel`/`gps_speed` are null forever, silently.

## 2 · MacLink.swift — two changes

**(a) The path.** `/ws/glasses` is hard-coded in `ingest.INGEST_PATH`; a bare `/` 404s
at the handshake. Change exactly this line in `init`, then dial
`MacLink(host: "<mac-lan-ip>", port: 8010)` — never `localhost`, which is the phone.

```swift
    url = URL(string: "ws://\(host):\(port)/")!            // before
    url = URL(string: "ws://\(host):\(port)/ws/glasses")!  // after
```

**(b) A raw send.** `send(_:)` wraps its argument in `{"type":"echo","text":…}`, so a
capture packet would arrive as an echo carrying JSON as a string and never decode. Add
this beside it — A11's `send` stays as it is:

```swift
  /// A14: a pre-built `wire` message, with no envelope of its own.
  func sendRaw(_ json: String) {
    task?.send(.string(json)) { if let e = $0 { print("send failed: \(e)") } }
  }
```

## 3 · Six lines at the DAT frame callback

`StreamConfiguration` stays at **2 fps** (valid 2/7/15/24/30): the phone samples one
frame per 1.5 s and drops the rest; more fps only costs glasses battery.

```swift
@State private var sender = CapturePacketSender()          // owns a PhoneSensors
sender.start { [weak link] json in link?.sendRaw(json) }   // after link.connect()

stream.videoFramePublisher.listen { frame in
    guard let image = frame.makeUIImage() else { return }
    sender.offer(image, at: Date())   // <-- the new line; thread-safe, throttles itself
}
```

## 4 · Verify from the Mac

`curl localhost:8010/ingest/stats` → `connected: 1`, `received` rising, `malformed: 0`.
Those are `phone_connected` / `packets` on `GET /health` of the standalone ingest
server (`longevity.server.app`, port 8000). `malformed` rising instead of `received`
means step 2(b) was skipped.
