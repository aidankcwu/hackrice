# Checkpoint 1 — Xcode session (A4, A11, A13)

Glasses are needed for **A4 only**. A11 and A13 need the iPhone but not the glasses, so
if DAT goes unavailable mid-session you can keep working.

Do A4 first. DAT availability is transient (hardware_software.md §24) — record the
corpus while the session is known good rather than assuming it will be there tomorrow.

---

## A4 · Record the replay corpus

Throwaway code on top of the working `CameraAccess` build. Nothing here is
version-controlled; it gets deleted once A12–A14 exist.

### Step 1 — add the recorder

New file in the `CameraAccess` target, `CorpusRecorder.swift`:

```swift
import UIKit

/// Throwaway corpus recorder for task A4. Deleted once A12-A14 send real packets.
final class CorpusRecorder: ObservableObject {
    @Published private(set) var isRecording = false
    @Published private(set) var count = 0

    private var lastSave: TimeInterval = 0
    private let interval: TimeInterval = 1.0
    private let queue = DispatchQueue(label: "corpus.recorder", qos: .utility)

    private lazy var dir: URL = {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let d = docs.appendingPathComponent("corpus", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }()

    func start() { count = 0; lastSave = 0; isRecording = true }
    func stop()  { isRecording = false }

    /// Call from the DAT frame listener. Returns immediately; encoding is off-thread.
    func offer(_ image: UIImage) {
        guard isRecording else { return }
        let now = Date().timeIntervalSince1970
        guard now - lastSave >= interval else { return }   // 2 fps in, 1 Hz out
        lastSave = now
        let millis = Int64((now * 1000).rounded())
        queue.async { [weak self] in
            guard let self, let jpeg = Self.encode(image) else { return }
            try? jpeg.write(to: self.dir.appendingPathComponent("frame_\(millis).jpg"))
            DispatchQueue.main.async { self.count += 1 }
        }
    }

    /// 512 px long edge, quality 0.70 — matches SPEC §2.2 exactly, so sensor values
    /// measured on this corpus stay comparable to the live glasses path later.
    private static func encode(_ image: UIImage) -> Data? {
        let maxEdge: CGFloat = 512
        let w = image.size.width, h = image.size.height
        let scale = min(maxEdge / max(w, h), 1.0)
        let size = CGSize(width: (w * scale).rounded(), height: (h * scale).rounded())
        let resized = UIGraphicsImageRenderer(size: size).image { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
        return resized.jpegData(compressionQuality: 0.70)
    }
}
```

**The filename format `frame_<unix_millis>.jpg` is a fixed contract** — the Python
replay adapter parses it. Don't change it.

### Step 2 — hook it into the frame listener

In `CameraAccess/Views/CameraView.swift`, find the existing DAT frame listener
(`videoFramePublisher.listen { frame in ... }`, per hardware_software.md §12/§30) and
add one line where the frame is already converted to a `UIImage`:

```swift
stream.videoFramePublisher.listen { frame in
    guard let image = frame.makeUIImage() else { return }
    recorder.offer(image)          // <-- add
    // ...existing preview code...
}
```

Add the state object and two buttons to the view:

```swift
@StateObject private var recorder = CorpusRecorder()

// in the body, near the existing preview/photo controls:
Button(recorder.isRecording ? "Stop (\(recorder.count) frames)" : "Record corpus") {
    recorder.isRecording ? recorder.stop() : recorder.start()
}
```

### Step 3 — make the files retrievable

In the target's Info tab add these two keys. Without them the Documents folder is
invisible and you'll be stuck AirDropping ~500 files one at a time:

| Key | Value |
|---|---|
| `UIFileSharingEnabled` (Application supports iTunes file sharing) | `YES` |
| `LSSupportsOpeningDocumentsInPlace` | `YES` |

Then pull the whole folder in one drag: connect the iPhone, open **Finder → iPhone →
Files → CameraAccess**, drag `corpus` out. Far better than AirDrop for this volume.

### Step 4 — shoot the sequence

Start the DAT session and preview first, confirm frames are live, *then* hit Record.

Scripted sequence, roughly 90 s per segment:

1. **Seated indoors** — at a desk, no screen in view, just sitting.
2. **Food appears** — put a meal and a coffee in frame, eat for a bit.
3. **Outdoors** — walk outside, include greenery/trees, look around, pass some people.
4. **Screen block** — sit back down, laptop or monitor filling a good part of the frame.

Then **stop, start again, and shoot the whole thing a second time.** Re-shooting later
costs a charge cycle and another window of working DAT.

Two things that will cost you:
- **Battery.** Continuous streaming drains the glasses in well under an hour. Two runs
  at ~6 min each is fine; don't leave the stream running between takes.
- **Head motion matters.** Move your head naturally. The corpus is what `frame_delta`,
  `flow_mag`, and `phash` get tuned against, and a corpus shot standing perfectly still
  produces thresholds that fire constantly the moment you actually walk.

**Done when:** a `corpus/` directory of `frame_<millis>.jpg` files is on the Mac and
Person B has a copy.

---

## A11 · Prove the socket  *(iPhone only — no glasses)*

### Step 1 — the two Info.plist keys

| Key | Value |
|---|---|
| `NSLocalNetworkUsageDescription` | `Sends captured frames to the paired Mac on your local network.` |
| `NSAppTransportSecurity` → `NSAllowsLocalNetworking` | `YES` |

Without both, the WebSocket **fails silently** in a way that reads exactly like a
Python bug. Add them before you write a line of socket code.

### Step 2 — start the Mac side

```
cd /Users/aidanwu/hackrice
uv run python tools/echo_server.py
```

Binds `0.0.0.0:8765` and prints its own LAN IP. It logs every inbound message, and for
a capture packet logs the decoded JPEG size rather than the base64 blob. Anything you
type on stdin is sent to the phone as a `speak` message — which is also how A16 gets
tested later.

Your Mac is **`ws://10.135.100.7:8765/`**. Never `localhost` — that resolves to the
phone itself.

### Step 3 — the Swift client

```swift
import Foundation

final class MacLink: NSObject, ObservableObject {
    @Published private(set) var connected = false
    @Published private(set) var lastFromMac = ""

    private var task: URLSessionWebSocketTask?
    private lazy var session = URLSession(configuration: .default,
                                          delegate: self, delegateQueue: nil)
    private let url: URL

    init(host: String = "10.135.100.7", port: Int = 8765) {
        self.url = URL(string: "ws://\(host):\(port)/")!
        super.init()
    }

    func connect() {
        let t = session.webSocketTask(with: url)
        task = t
        t.resume()
        receive()
    }

    func disconnect() {
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        connected = false
    }

    func send(_ text: String) {
        task?.send(.string(text)) { if let e = $0 { print("send error: \(e)") } }
    }

    /// `receive` is ONE-SHOT. Forgetting to re-arm it is the classic bug here: the
    /// first message from the Mac arrives, every one after it is silently ignored.
    private func receive() {
        task?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let message):
                if case .string(let text) = message {
                    print("from Mac: \(text)")
                    DispatchQueue.main.async { self.lastFromMac = text }
                }
                self.receive()
            case .failure(let error):
                print("socket closed: \(error)")
                DispatchQueue.main.async { self.connected = false }
            }
        }
    }
}

extension MacLink: URLSessionWebSocketDelegate {
    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didOpenWithProtocol protocol: String?) {
        print("connected to \(url)")
        DispatchQueue.main.async { self.connected = true }
    }
    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
                    reason: Data?) {
        DispatchQueue.main.async { self.connected = false }
    }
}
```

Add a button that calls `link.connect()` and another that calls
`link.send("hello from the phone")`.

**Done when:** the Mac terminal prints your string, and a line you type on the Mac
prints in the Xcode console.

If nothing arrives: the two plist keys, then the LAN IP, then whether both devices are
on the same Wi-Fi. In that order — it is almost always the plist.

---

## A13 · Phone sensors  *(iPhone only — no glasses)*

Two more Info.plist keys:

| Key | Value |
|---|---|
| `NSLocationWhenInUseUsageDescription` | `Uses your speed to tell walking from sitting.` |
| `NSMotionUsageDescription` | `Uses motion to tell stillness from activity.` |

### Use the RAW accelerometer, not `userAcceleration`

This is the one that will silently ruin the data. The Mac derives `accel_rms` from
`|accel| − 1g`, which assumes gravity is **included**. `deviceMotion.userAcceleration`
has gravity already removed, so every magnitude collapses toward zero and `accel_rms`
reads a near-constant. There is a warning logged on the Mac if it detects this, but the
fix is to send `CMAccelerometerData.acceleration` and nothing else.

### Send a burst, not a single sample

At 1 Hz, one accelerometer sample per packet means the Mac's window spans 8 real
seconds — it cannot see a step, a head turn, or you standing up until seconds later,
and one sample cannot tell "moving" from "tilted" at all. CoreMotion at 20 Hz is
nearly free, and ~20 samples is ~500 bytes against a 53 KB packet.

So collect at 20 Hz and ship the last second with each packet. `accel` stays as the
single latest sample.

```swift
import CoreLocation
import CoreMotion

final class PhoneSensors: NSObject, ObservableObject {
    private let motion = CMMotionManager()
    private let locator = CLLocationManager()
    private let lock = NSLock()
    private var ring: [[Double]] = []

    private(set) var latest: [Double] = [0, 0, 0]
    private(set) var speed: Double?

    func start() {
        motion.accelerometerUpdateInterval = 1.0 / 20.0
        motion.startAccelerometerUpdates(to: .main) { [weak self] data, _ in
            guard let self, let a = data?.acceleration else { return }
            let sample = [a.x, a.y, a.z]      // RAW: gravity included
            self.latest = sample
            self.lock.lock()
            self.ring.append(sample)
            if self.ring.count > 25 { self.ring.removeFirst(self.ring.count - 25) }
            self.lock.unlock()
        }
        locator.delegate = self
        locator.requestWhenInUseAuthorization()
        locator.desiredAccuracy = kCLLocationAccuracyBest
        locator.startUpdatingLocation()
    }

    /// Take the last second of samples and clear. Drop, never queue.
    func drainBurst() -> [[Double]] {
        lock.lock(); defer { lock.unlock() }
        let out = ring
        ring.removeAll(keepingCapacity: true)
        return out
    }
}

extension PhoneSensors: CLLocationManagerDelegate {
    func locationManager(_ m: CLLocationManager, didUpdateLocations locs: [CLLocation]) {
        guard let l = locs.last else { return }
        // CoreLocation reports -1 for "speed unknown". Sending that raw would read as
        // travelling backwards at 1 m/s on the Mac.
        speed = l.speed >= 0 ? l.speed : nil
    }
}
```

**Done when:** plausible values print to the Xcode console — `accel` roughly
`(0, 0, -1)` flat on a table, `speed` near 0 seated and ~1.4 m/s walking.

---

## One more gotcha, for A14 later

When you base64 the JPEG, use `data.base64EncodedString()` with **no options**.
`.lineLength64Characters` inserts newlines, and the Mac validates strictly — every
frame would be silently rejected as malformed.
