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
climbing alone = `isConnected` is false. Two segments appear only when non-zero: `busy N`
(frames dropped because the previous capture send had not completed; at most one is ever
outstanding) and `stalled N` (sends that missed the 3 s deadline). Capture `link` strongly — a weak one that went
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

## 7 Reconnect and host configuration

`MacLink` now heals itself. Nothing in the public API changed — `connect()`,
`disconnect()`, `send(_:)`, `sendRaw(_:completion:)`, `connected`, `status`,
`sentCount`, `spokenCount`, `lastFromMac` all mean what they meant — so the wiring in
§3 is unchanged.

- **Intent, not state.** `connect()` sets an internal `wantConnected` flag; only
  `disconnect()` clears it. Any receive or send failure while it is set schedules one
  reconnect with exponential backoff — 1, 2, 4, 8 s, capped at 10 s, plus <0.3 s of
  jitter — and `status` reads `closed · reconnecting in 4s (attempt 3)` while it waits.
  There is never more than one attempt pending, and `disconnect()` cancels it.
- **`connected` is now confirmed, not assumed.** It flips true when the hello send
  *completes* with no error, not when `resume()` returns — `resume()` succeeds against a
  Mac that is switched off. The backoff resets at that same moment. The sample view's
  existing `.onChange(of: link.connected)` (stop on false, `capture.start(send:)` on
  true) already does the right thing on a reconnect — `start()` re-emits `hello`, which
  is per-connection — so no view change is needed. If you keep the sender running across
  a drop instead, call `sender.announce()` when it comes back.
- **Keepalive.** While connected the phone sends `{"v":1,"type":"ping"}` every 10 s. The
  Mac closes sockets idle for 30 s, and this socket is idle for exactly as long as the
  glasses aren't streaming yet. Pings do not move `sentCount` — that number stays
  comparable with the Mac's `received`.
- **Failure statuses keep their prefixes** (`not connected` / `send failed` / `closed`)
  so `apply(macLinkStatus:)` still reads them as down; that is why the reconnect
  countdown is prefixed `closed · `. Binding `link.connected` is still the better wire.
- **Host and port are runtime settings**, persisted in `UserDefaults` under `macHost`
  and `macPort` and read back in `init`. `MacLink.defaultHost`/`defaultPort` are only
  the first guess on a fresh install. A venue network change is now a text field, not
  an Xcode rebuild on someone else's Mac.

```swift
@State private var hostField = ""      // seed with link.host in .onAppear
@State private var portField = "8010"

HStack {
    TextField("Mac IP", text: $hostField)
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        .keyboardType(.decimalPad)
    TextField("Port", text: $portField).frame(width: 70).keyboardType(.numberPad)
    Button("Apply") { link.configure(host: hostField, port: Int(portField) ?? 8010) }
}
Text(link.status).font(.caption)   // shows "connected 10.0.0.5:8010" or the countdown
```

`configure` stores both, rebuilds the URL, and reconnects immediately if the link was
connected (or trying to be); an empty host or an out-of-range port is rejected and
leaves the current setting *and* `status` alone — the rejection lands on
`link.configError` (nil when the last apply was valid), so show that next to the field.
While a socket is being (re)opened `status` reads `not connected · connecting …` until
the hello is confirmed, which `apply(macLinkStatus:)` correctly treats as down. `ipconfig getifaddr en0` on the Mac gives the value to
type.

**`ios/xcode-project.patch` is stale** until re-snapshotted per §5 — it predates these
`MacLink.swift` changes.

## 8 Ask and answer

Add `QuestionListener.swift` to the `CameraAccess` target. `NSMicrophoneUsageDescription`
already exists; also add `NSSpeechRecognitionUsageDescription` (for example, `Transcribes
short answers to questions asked through the glasses.`). iOS will terminate or deny the
request if either purpose string is absent.

The listener exposes the explicit state machine `idle → awaitingPlayback(question) →
listening(question) → sending → idle`. `MacLink` moves it past awaiting only after the
preceding AVSpeechSynthesizer or AVAudioPlayer completion callback; with no playback in
flight it keeps a 300 ms beat between the wire message and the microphone.

Show it in the existing view with one line:

```swift
Text(link.listener.statusLine)
```

Listening temporarily changes the audio session from output-only A2DP to
`.playAndRecord` with both `.allowBluetooth` and `.allowBluetoothA2DP`. That permits iOS
to select the glasses microphone using the Bluetooth hands-free profile, which can make
the output route audibly switch away from high-quality A2DP. The microphone is open only
for the answer window because that profile switch costs quality, battery, and privacy;
every result, timeout, interruption, route change, cancellation, stop, and socket failure
tears down recognition and restores `.playback` / `.spokenAudio` / A2DP.

The checked-in Xcode patch is stale until the working target is re-snapshotted using the
§5 commands. On a physical device, confirm the logged input is the Ray-Ban hands-free
route, note how long the profile switch takes, and check whether DAT video frames pause
while that microphone route is active; the existing hardware validation did not cover
simultaneous DAT camera input and a custom Bluetooth microphone pipeline.

## 9 Hosted backend, token, consent (TestFlight)

Testers are not on our Wi-Fi, so the phone dials a hosted backend over TLS. The whole
tester onboarding is one pasted URL:

```
wss://DOMAIN/t/NAME/ws/glasses?token=TOKEN
```

**Files.** Replace `MacLink.swift` and `CapturePacketSender.swift`, and add
`ConsentView.swift` to the `CameraAccess` target. `ConsentView.swift` is required, not
optional: `MacLink.connect()` and `CapturePacketSender.start()` both read
`StreamingConsent.isGranted`, so a build without it does not compile, and a build that
never shows the sheet never connects (`status` reads `not connected · consent needed`).

**What MacLink does now.** The public API from §7 is unchanged, and so is every message
on the wire (`hello`, `capture`, `answer`, `ping`, `echo`).

- `configure(serverURL:)` stores the URL, token included, under the UserDefaults key
  `serverURL`. A parseable stored URL **wins over** `macHost`/`macPort`. `https://` and
  `http://` are accepted and mapped to `wss://`/`ws://`, and an empty or `/` path gets
  `/ws/glasses`. An empty string clears the URL and falls back to host/port.
  `configure(host:port:)` still works and now also clears `serverURL`, because choosing a
  LAN address means choosing LAN mode.
- The token is sent twice: in the query (it is part of the URL) and as the
  `X-Access-Token` header on the upgrade request.
- `sendRaw(_:deadline:completion:)` has a send deadline, `MacLink.sendDeadline` (3 s)
  by default. A send that has not completed by then gets `URLError(.timedOut)`, and the
  socket is torn down and reconnected with the §7 backoff. The existing call
  `link.sendRaw(json, completion: done)` is unchanged. `CapturePacketSender` keeps at
  most one capture send outstanding and drops frames (`busy`) while it waits.
- **Token rejected** means the server closed with **4401**, or answered the handshake
  with HTTP **401/403**. MacLink then withdraws `wantConnected` and cancels any pending
  reconnect, so it stops dialling. It sets `accessDenied = true` and `status` to
  `not connected · invalid access token — re-paste the server URL`. The next attempt
  only happens when someone applies a new URL or taps Connect. Any other drop reconnects
  with the §7 backoff, as before.
- New observables: `serverURL` (seed the text field from it), `endpointLabel`
  (token-free, such as `wss://DOMAIN/t/NAME/ws/glasses`; show this, never the URL), and
  `accessDenied`. `host`/`port` still exist; in URL mode they hold the URL's host and
  443.
- Status strings keep the `not connected` / `closed` / `send failed` prefixes, so
  `apply(macLinkStatus:)` still reads every new failure as down. `connected …` now shows
  `endpointLabel`.

| UserDefaults key | Holds | Written by |
|---|---|---|
| `serverURL` | the pasted `wss://…?token=…` (a secret; fine for a demo token) | `configure(serverURL:)` |
| `macHost`, `macPort` | LAN dev fallback (unchanged) | `configure(host:port:)` |
| `streamingConsent.v2` | `true` once the user tapped I agree (v2: accurate retention and transcription text; v1 agreements are asked again) | `ConsentView` |

**Settings UI.** In `CameraView.swift`, replace the `Mac IP` / `Port` `HStack` inside
`if showSetup { … }` with the field below. Delete `hostField` and `portField`, and reuse
the existing `addressFieldFocused` so the keyboard toolbar keeps working.

```swift
@State private var serverField = ""     // seeded from link.serverURL in .onAppear
@State private var askConsent = false

HStack(spacing: 6) {
  TextField("Server URL", text: $serverField)
    .textInputAutocapitalization(.never)
    .autocorrectionDisabled()
    .keyboardType(.URL)
    .textContentType(.URL)
    .submitLabel(.done)
    .focused($addressFieldFocused)
    .onSubmit { link.configure(serverURL: serverField) }
  Button("Apply") {
    link.configure(serverURL: serverField)
    addressFieldFocused = false
  }
  .font(.system(size: 14, weight: .semibold))
}
// ...keep the existing .font/.padding/.background/.toolbar modifiers on the HStack.

if link.accessDenied {
  Text("Invalid access token. Paste the URL you were sent again.")
    .font(.system(size: 12, weight: .semibold))
    .foregroundStyle(.orange)
}
// The existing `if let error = link.configError { … }` stays as it is.
```

Also make these changes in the same file:

- In the Setup disclosure label, replace `Text("\(link.host):\(link.port)")` with
  `Text(link.endpointLabel)`, which shows no token.
- In `.onAppear`, replace the host/port seeding with the lines below. They open Setup on
  a fresh install, so a tester sees the field without hunting for it:
  ```swift
  if serverField.isEmpty { serverField = link.serverURL }
  if link.serverURL.isEmpty { showSetup = true }
  ```
- In the Connect button action, ask for consent the first time:
  ```swift
  if link.connected {
    link.send("ping \(Int(Date().timeIntervalSince1970))")
  } else if !StreamingConsent.isGranted {
    askConsent = true
  } else {
    link.connect()
  }
  ```
  Relabel it `Connect` / `Connected`. There is no Mac any more.
- Attach the sheet once, on the bottom bar or the body root:
  ```swift
  .streamingConsentSheet(isPresented: $askConsent) { link.connect() }
  ```
- Recommended: keep the screen awake while connected, because a tester's phone
  auto-locking mid-demo is the likeliest way to lose the stream. Put this in the existing
  `.onChange(of: link.connected)`:
  `UIApplication.shared.isIdleTimerDisabled = isUp`.

The sheet shows once, the first time someone taps Connect. After I agree it connects
straight away and never shows again unless the app is deleted or
`StreamingConsent.revoke()` is called. With Not now, nothing is stored and Connect keeps
asking.

**Info.plist.**

- Keep `NSAllowsLocalNetworking`, which LAN dev still needs.
- Do **not** add `NSAllowsArbitraryLoads`. `wss://` to a host with a publicly trusted
  certificate needs no ATS exception. A self-signed certificate, or `ws://` to a public
  host, fails in the same silent way as §1, and that is the correct outcome.
- Add `ITSAppUsesNonExemptEncryption` = `NO`. The app only uses Apple's TLS, and without
  this key every upload stops at an export-compliance question.
- The privacy manifest and usage strings are in [TESTFLIGHT.md](../TESTFLIGHT.md) R4.

**Verify.**

- Local dev still works. Clear the field and Apply to get back to `macHost`/`macPort`,
  or paste `ws://<mac-ip>:8010/ws/glasses`.
- Against the hosted backend, `status` should read `connected wss://DOMAIN/t/NAME/ws/glasses`.
- With a wrong token it should read `invalid access token`, and the backend log should
  show one attempt per tap, not a retry every few seconds.
