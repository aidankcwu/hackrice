//  CapturePacketSender.swift
//  PERSON_A.md A12 (sample + encode) + A13 (sensors ride along) + A14 (send the packet).
//  Drop into the CameraAccess target alongside MacLink.swift and PhoneSensors.swift.
//
//  This is the whole phone-side product: sample, encode, send. No ticks, no VLM, no
//  logic (SPEC §11.4). The Mac derives everything; the phone computes nothing (§11.2).
//
//  Four rules this file exists to enforce:
//
//  1. **Drop, never queue** (SPEC §5.2). The DAT stream runs at 2 fps; one frame per
//     `interval` is encoded and every other frame is thrown away *at the door*. There
//     is no buffer here, and `encodeInFlight` makes sure a slow encode drops the next
//     frame rather than stacking work behind itself.
//  2. **Encoding is off the main actor.** CorpusRecorder (A4) encodes synchronously on
//     main because it is a throwaway at 1 Hz; this one is in the live path and shares
//     the main actor with the DAT preview, so the resize + JPEG runs on `capture.encode`.
//  3. **The wire shape is `src/longevity/wire.py`, exactly.** Field names, `t` rounded
//     to 3 dp, base64 with **no line breaks**. `ingest.parse_capture` validates
//     strictly and a malformed packet is silently counted as `malformed`, not reported.
//  4. **`sentCount` means *delivered*, not *handed over*.** The transport reports
//     completion and only a `nil` error moves the counter. Counting at the call site
//     is how you get a phone proudly reporting "sent 400" against a Mac reporting
//     `received: 0` — the two numbers must be comparable or neither is worth reading.
//     `isConnected` is the same idea one step earlier: no socket, no send, one drop.
//
//  The encode below is deliberately byte-identical to `CorpusRecorder.encode` — 512 px
//  longest edge, JPEG q0.7 — so every sensor threshold tuned against the A4 corpus
//  still means the same thing on the live path (SPEC §2.2). It is duplicated rather
//  than called so that this file stands alone when CorpusRecorder is deleted; if you
//  keep CorpusRecorder, delete *its* `encode` and call this one, so there is exactly
//  one encoder in the target.

import CoreImage
import CoreVideo
import Foundation
import Observation
import UIKit

/// File-scope so they are reachable from `nonisolated` members without actor hops.
/// `CIContext` is documented thread-safe; `DispatchQueue` is `Sendable`.
private enum CaptureEncode {
  static let queue = DispatchQueue(label: "capture.encode", qos: .userInitiated)
  static let ciContext = CIContext(options: [.useSoftwareRenderer: false])
}

@Observable
@MainActor
final class CapturePacketSender {

  /// SPEC §2.2: `TICK_INTERVAL_S`, 1.5 s as shipped — chosen so the Mac's VLM call
  /// usually returns inside one interval. `backend/.env.example` agrees (1.5).
  /// Note `src/longevity/sources/glasses.py` still defaults its own poll to 1.0 s, and
  /// `main.py` builds `GlassesSource(link)` with that default. The integrated process
  /// (Person B's app running T0 in-process) passes 1.5 explicitly, so the two agree
  /// there. Against a bare `uv run t0 --source glasses` the mismatch is harmless — the
  /// source skips empty intervals by design — but `n_idle` will climb at roughly one
  /// third of ticks, which is the expected reading, not a fault.
  nonisolated static var defaultInterval: TimeInterval { 1.5 }

  // MARK: - Status line (A14's proof, and the thing to read out during the demo)

  /// Packets the **transport confirmed**. Moved only by `delivered(bytes:failure:)`,
  /// from the completion handler, never at the call site — a handshake that 404s or a
  /// socket the Mac already closed otherwise shows a happily rising count here while
  /// `ingest/stats` sits at `received: 0`, which is an hour of debugging the Mac.
  private(set) var sentCount = 0
  /// Packets the transport reported an error for. `sentCount + failedCount` is the
  /// number of encodes that reached the socket; `sentCount` alone is the number that
  /// left it. A climbing `failedCount` with `sentCount` stuck is a dead socket.
  private(set) var failedCount = 0
  /// Frames the sampler discarded, plus packets refused because `isConnected` is false.
  /// At 2 fps into a 1.5 s interval this climbs ~2x as fast as `sentCount`. A zero here
  /// means frames are not arriving at all.
  private(set) var droppedCount = 0
  private(set) var lastSentAt: Date?
  private(set) var lastPacketBytes = 0
  private(set) var isRunning = false
  /// Set on encode failure, a missing transport, a closed socket, or a send error, so a
  /// dead path is never silent. Cleared only by a confirmed delivery.
  private(set) var lastError: String?

  /// The socket's state, mirrored in by the app — this class owns no transport and so
  /// cannot know it. While false every capture packet is refused and counted as
  /// dropped, because handing a packet to a dead `URLSessionWebSocketTask` succeeds
  /// loudly and delivers nothing.
  ///
  /// Wire it to MacLink, either directly:
  ///
  ///     sender.isConnected = link.connected
  ///
  /// or from its status string via `apply(macLinkStatus:)` below.
  var isConnected = false

  /// One line for the camera view. `~53 KB` is the expected packet size: a ~40 KB JPEG
  /// plus base64's 33% (wire.py says so explicitly).
  var statusLine: String {
    let kb = lastPacketBytes > 0 ? "\(lastPacketBytes / 1024) KB" : "—"
    let when = lastSentAt.map { String(format: "%.1fs ago", Date().timeIntervalSince($0)) }
    return "sent \(sentCount) · failed \(failedCount) · dropped \(droppedCount) · \(kb)"
      + " · \(when ?? "never")" + (lastError.map { " · \($0)" } ?? "")
  }

  /// Derive `isConnected` from `MacLink.status`.
  ///
  /// Matching `hasPrefix("connected")` alone is a trap: MacLink's own `connect()` sets
  /// `status = "connected <host>:<port>"` and then immediately sends its A11 echo
  /// hello, whose completion overwrites it with `"sent 1"` — so a prefix test would
  /// latch us off milliseconds after connecting and nothing would ever be sent. The
  /// failure strings are the stable signal, and MacLink only ever writes three of them.
  func apply(macLinkStatus status: String) {
    isConnected =
      !(status.hasPrefix("not connected") || status.hasPrefix("send failed")
        || status.hasPrefix("closed"))
  }

  // MARK: - Wiring

  @ObservationIgnored let sensors: PhoneSensors
  @ObservationIgnored let interval: TimeInterval

  /// The transport. Wire it to `MacLink.sendRaw` — **not** `MacLink.send`, which wraps
  /// its argument in an `echo` envelope; a capture packet inside an echo arrives on the
  /// Mac as `{"type":"echo","text":"{…}"}` and is logged, never decoded. See INTEGRATION.md.
  ///
  /// The second parameter is the delivery report and it is **not optional**: the
  /// transport must call it exactly once, with `nil` on success and the error
  /// otherwise. `URLSessionWebSocketTask.send` fires it on a background queue, so it is
  /// `@Sendable` and this class hops back to the main actor inside it.
  @ObservationIgnored
  var send: (@MainActor (String, @escaping @Sendable (Error?) -> Void) -> Void)?

  @ObservationIgnored private var lastSampleAt: TimeInterval = 0
  @ObservationIgnored private var encodeInFlight = false

  /// `nonisolated` so SwiftUI can build one in a property initializer
  /// (`@State private var sender = CapturePacketSender()`), which is not a main-actor
  /// context. Construct it on the main thread anyway: `PhoneSensors` creates a
  /// `CLLocationManager`, which wants the thread it was made on to have a run loop.
  nonisolated init(
    sensors: PhoneSensors = PhoneSensors(),
    interval: TimeInterval = CapturePacketSender.defaultInterval
  ) {
    self.sensors = sensors
    self.interval = interval
  }

  // MARK: - Lifecycle

  /// Start sampling and announce ourselves. Call after `MacLink.connect()`, and set
  /// `isConnected` *first* — `start()` emits `hello`, which is refused while it is false.
  func start(
    send: @escaping @MainActor (String, @escaping @Sendable (Error?) -> Void) -> Void
  ) {
    self.send = send
    start()
  }

  /// Re-announce on a reconnect. `hello` is per-connection (wire.py: "sent once on
  /// connect"), and a new socket is a new connection. Cosmetic — `ingest._handle` only
  /// logs it and `parse_capture` never looks for it — so skipping this costs a log line.
  func announce() {
    emit(Self.helloJSON())
  }

  func start() {
    guard !isRunning else { return }
    sensors.start()
    lastSampleAt = 0
    encodeInFlight = false
    lastError = nil
    isRunning = true
    // wire.py: HELLO is "sent once on connect; identifies the phone, carries clock
    // offset". `ingest._handle` logs the whole message, so extra keys are free.
    emit(Self.helloJSON())
  }

  func stop() {
    isRunning = false
    sensors.stop()
  }

  // MARK: - Frame intake (A12)

  /// The DAT entry point. `hardware_software.md` §12/§32: frames arrive as `VideoFrame`
  /// and the SDK's own conversion is `frame.makeUIImage()`, so this is the form the
  /// stream listener actually has in hand:
  ///
  ///     stream.videoFramePublisher.listen { frame in
  ///         guard let image = frame.makeUIImage() else { return }
  ///         sender.offer(image, at: Date())
  ///     }
  ///
  /// `nonisolated` on purpose: the DAT listener's thread is not documented, and a
  /// main-actor-only entry point would be a compile error from a background callback
  /// and a hidden hop from the main one. Sampling happens after the hop, on the main
  /// actor, where `lastSampleAt` lives — so there is exactly one gate and no lock.
  nonisolated func offer(_ image: UIImage, at t: Date = Date()) {
    Task { @MainActor [weak self] in
      self?.ingest(image, at: t)
    }
  }

  /// Alternate intake for a raw buffer. Provided because `VideoFrame`'s underlying
  /// pixel format is an SDK implementation detail the architecture does not depend on
  /// (SPEC §2.2) — if a future SDK hands you a `CVPixelBuffer` instead of a `UIImage`,
  /// nothing above this line changes.
  ///
  /// The conversion is **eager and synchronous**, unlike the UIImage path: a buffer
  /// vended by a capture pipeline is typically recycled the moment the callback
  /// returns, so deferring the read past the actor hop would sample the next frame's
  /// pixels, or garbage. At 2 fps a 504×896 `createCGImage` is a few ms.
  nonisolated func offer(pixelBuffer: CVPixelBuffer, at t: Date = Date()) {
    guard let image = Self.makeImage(from: pixelBuffer) else { return }
    offer(image, at: t)
  }

  /// Main-actor: gate, then hand the survivor to the encode queue.
  private func ingest(_ image: UIImage, at t: Date) {
    guard isRunning else { return }
    let now = t.timeIntervalSince1970

    // Drop, never queue (SPEC §5.2). Both guards discard — neither defers work.
    guard now - lastSampleAt >= interval else {
      droppedCount += 1
      return
    }
    guard !encodeInFlight else {
      droppedCount += 1
      return
    }
    lastSampleAt = now
    encodeInFlight = true

    // Read the sensors here, on the main actor, so the values in the packet are the
    // ones that were true when the frame was *taken*, not when the encode finished.
    let accel = sensors.latest
    let burst = sensors.burst
    let speed = sensors.speed

    CaptureEncode.queue.async { [weak self] in
      let json = CapturePacketSender.capturePacketJSON(
        t: now, image: image, gpsSpeed: speed, accel: accel, burst: burst)
      Task { @MainActor in
        self?.finish(json)
      }
    }
  }

  private func finish(_ json: String?) {
    encodeInFlight = false
    guard let json else {
      lastError = "encode failed"
      return
    }
    guard let send else {
      lastError = "no transport wired — call start(send:)"
      return
    }
    guard isConnected else {
      // Refusing is the honest answer. `task.send` on a socket whose handshake failed
      // still calls back with `nil` on some paths, so "we handed it over" is not proof
      // of anything; a drop the operator can see is worth more than a fake success.
      droppedCount += 1
      lastError = "not connected"
      return
    }
    let bytes = json.utf8.count
    send(json) { [weak self] error in
      // Flatten the error to a String *before* the hop: a bare `Error` existential is
      // not Sendable, and capturing one in a `Task { @MainActor in … }` is a strict
      // concurrency error. The message is all `statusLine` ever wanted anyway.
      let failure = error.map { "send failed: \($0.localizedDescription)" }
      Task { @MainActor in
        self?.delivered(bytes: bytes, failure: failure)
      }
    }
  }

  /// The only place `sentCount`, `lastSentAt` and `lastPacketBytes` move.
  private func delivered(bytes: Int, failure: String?) {
    if let failure {
      failedCount += 1
      lastError = failure
      return
    }
    sentCount += 1
    lastPacketBytes = bytes
    lastSentAt = Date()
    lastError = nil
  }

  // MARK: - Mac -> phone

  /// Answer a `ping` with a `pong` (wire.py). Everything else is ignored here —
  /// `speak` is MacLink's job (A16) and `audio` is A18's.
  ///
  /// MacLink does not surface inbound messages today. One line inside its private
  /// `handle(_ raw: String)`, right after `lastFromMac` is assigned, wires this up:
  ///
  ///     onMessage?(raw)                     // add `var onMessage: ((String) -> Void)?`
  ///
  /// then, wherever you build the two objects:
  ///
  ///     link.onMessage = { [weak sender] raw in sender?.handleInbound(raw) }
  ///
  /// Nothing breaks without it: `ingest` never sends `ping` on its own, so this is
  /// keep-alive insurance, not part of the capture path.
  func handleInbound(_ raw: String) {
    guard let data = raw.data(using: .utf8),
      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
      obj["type"] as? String == "ping"
    else { return }
    emit(Self.pongJSON())
  }

  /// Out-of-band messages (`hello`, `pong`). These are not capture packets, so they do
  /// not move `sentCount` — but a failure still surfaces, because a `hello` that never
  /// arrives is the first evidence the socket was never really up.
  private func emit(_ json: String?) {
    guard let json, let send else { return }
    guard isConnected else {
      lastError = "not connected"
      return
    }
    send(json) { [weak self] error in
      guard let failure = error.map({ "send failed: \($0.localizedDescription)" }) else { return }
      Task { @MainActor in
        self?.lastError = failure
      }
    }
  }

  // MARK: - Encoding (nonisolated: runs on capture.encode)

  /// Resize to `maxEdge` on the longest side and JPEG-encode. Never upscales.
  /// Byte-identical to `CorpusRecorder.encode` — see the header for why that matters.
  nonisolated static func encode(
    _ image: UIImage, maxEdge: CGFloat = 512, quality: CGFloat = 0.7
  ) -> Data? {
    let size = image.size
    guard size.width > 0, size.height > 0 else { return nil }
    let scale = min(1.0, maxEdge / max(size.width, size.height))
    let target = CGSize(
      width: (size.width * scale).rounded(),
      height: (size.height * scale).rounded())

    let format = UIGraphicsImageRendererFormat.default()
    format.scale = 1  // points == pixels, or a 3x device triples the file size
    format.opaque = true  // a JPEG has no alpha channel anyway
    let resized = UIGraphicsImageRenderer(size: target, format: format).image { _ in
      image.draw(in: CGRect(origin: .zero, size: target))
    }
    return resized.jpegData(compressionQuality: quality)
  }

  nonisolated static func makeImage(from pixelBuffer: CVPixelBuffer) -> UIImage? {
    let ci = CIImage(cvPixelBuffer: pixelBuffer)
    guard let cg = CaptureEncode.ciContext.createCGImage(ci, from: ci.extent) else { return nil }
    return UIImage(cgImage: cg)
  }

  // MARK: - The wire (A14) — mirrors src/longevity/wire.py exactly

  nonisolated static var protocolVersion: Int { 1 }

  /// `{"v":1,"type":"capture","t":…,"image":…,"gps_speed":…,"accel":…,"accel_burst":…}`
  ///
  /// `gps_speed` and `accel` are **null when unknown, not omitted** — `wire.capture_packet`
  /// always writes both keys, and `ingest._as_float` / `_as_accel` already return None
  /// for a null. `accel_burst` is the one optional key: omitted when empty, because
  /// `ingest._as_burst` treats a non-list as empty anyway and an empty list would just
  /// cost bytes.
  nonisolated static func capturePacketJSON(
    t: TimeInterval,
    image: UIImage,
    gpsSpeed: Double?,
    accel: PhoneSensors.Accel?,
    burst: [[Double]]
  ) -> String? {
    guard let jpeg = encode(image) else { return nil }

    var body: [String: Any] = [
      "v": protocolVersion,
      "type": "capture",
      // round(t, 3) in wire.py. Milliseconds is all anything downstream reads.
      "t": (t * 1000).rounded() / 1000,
      // NO options. `.lineLength64Characters` inserts newlines and `wire.decode_image`
      // validates strictly (`validate=True`) — every frame would be silently rejected.
      "image": jpeg.base64EncodedString(),
    ]
    if let gpsSpeed, gpsSpeed.isFinite {
      body["gps_speed"] = gpsSpeed
    } else {
      body["gps_speed"] = NSNull()
    }
    if let accel {
      let vector: [String: Double] = ["x": accel.x, "y": accel.y, "z": accel.z]
      body["accel"] = vector
    } else {
      body["accel"] = NSNull()
    }
    if !burst.isEmpty {
      body["accel_burst"] = burst
    }
    return json(body)
  }

  nonisolated static func helloJSON() -> String? {
    json([
      "v": protocolVersion,
      "type": "hello",
      "device": "iphone",
      // We do not measure skew, and claiming a number we did not measure is worse than
      // claiming none. The Mac falls back to arrival time past ±60 s anyway
      // (`ingest.MAX_CLOCK_SKEW_S`), and both machines are NTP-synced in practice.
      "clock_offset_s": 0,
    ])
  }

  nonisolated static func pongJSON() -> String? {
    json(["v": protocolVersion, "type": "pong", "t": (Date().timeIntervalSince1970 * 1000).rounded() / 1000])
  }

  private nonisolated static func json(_ body: [String: Any]) -> String? {
    guard JSONSerialization.isValidJSONObject(body),
      let data = try? JSONSerialization.data(withJSONObject: body)
    else { return nil }
    return String(data: data, encoding: .utf8)
  }
}
