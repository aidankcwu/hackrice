//  MacLink.swift
//  PERSON_A.md A11 — prove the phone <-> Mac socket before any payload rides on it.
//
//  Kept deliberately small. A11 exists as its own task because without
//  NSLocalNetworkUsageDescription and NSAppTransportSecurity -> NSAllowsLocalNetworking
//  in Info.plist, URLSessionWebSocketTask fails *silently* in a way that reads exactly
//  like a Python bug — and you lose an hour debugging the wrong half of the system.
//
//  No URLSessionWebSocketDelegate on purpose: the delegate buys only a didOpen callback,
//  and in exchange costs an NSObject subclass plus actor-isolation friction against the
//  sample's @Observable/@MainActor style. Send and receive prove both directions without it.
//  Reconnect does not need it either — a dead socket announces itself as a receive or
//  send failure, which is the same signal a didClose callback would carry.
//
//  A14 replaces `send(_:)`'s payload with a real capture packet. The transport stays.
//
//  ## Demo resilience (the two live-demo hazards this file used to own)
//
//  1. **Nothing reconnected.** A Wi-Fi blip set `connected = false` and stopped there;
//     someone had to tap "Connect to Mac" again, on stage. Now `connect()` records the
//     *intent* to be connected (`wantConnected`) and every failure path re-dials with
//     exponential backoff until `disconnect()` withdraws that intent. The socket is
//     also kept alive with a `ping` every 10 s, because the Mac closes sockets idle for
//     30 s and the phone is idle for exactly as long as the glasses aren't streaming.
//  2. **The Mac's IP was a compile-time constant.** A venue network change meant an
//     Xcode rebuild — on a teammate's Mac, in a different building. `configure(host:port:)`
//     now rewrites it at runtime and persists it in UserDefaults, so the defaults below
//     are only the first guess, never the last word.
//
//  Failure statuses keep their old prefixes ("not connected" / "send failed" / "closed")
//  because `CapturePacketSender.apply(macLinkStatus:)` pattern-matches exactly those to
//  derive `isConnected`. A reconnect countdown that read "reconnecting in 4s …" with no
//  prefix would look *connected* to that heuristic, and the sender would report failures
//  where it should report drops. Prefer binding `link.connected` directly where you can.

import AVFoundation
import Foundation
import Observation

private final class SpeechCompletionDelegate: NSObject, AVSpeechSynthesizerDelegate {
  var didFinish: ((AVSpeechUtterance) -> Void)?
  func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
    didFinish?(utterance)
  }
  func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
    didFinish?(utterance)
  }
}

private final class AudioCompletionDelegate: NSObject, AVAudioPlayerDelegate {
  var didFinish: ((AVAudioPlayer) -> Void)?
  func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
    didFinish?(player)
  }
  func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
    didFinish?(player)
  }
}

@Observable
@MainActor
final class MacLink {
  /// True once the hello send is *confirmed* by the transport, not merely when the task
  /// is resumed: `webSocketTask.resume()` succeeds against a Mac that is switched off.
  /// A dead Mac shows up as a send/receive failure, which is what clears this again.
  var connected = false
  /// Human-readable state for the button label — this is a debugging tool.
  var status = "not connected"
  /// Last rejected `configure(host:port:)` input, or nil. Kept apart from `status` on
  /// purpose (see `configure`).
  var configError: String?
  /// Most recent message the Mac pushed to us. Proves Mac -> phone.
  var lastFromMac = "—"
  var sentCount = 0
  /// Utterances spoken out the glasses. A16's proof.
  var spokenCount = 0

  /// Where we are dialling right now. Observable so a settings field can show it.
  private(set) var host: String
  private(set) var port: Int

  @ObservationIgnored private let synth = AVSpeechSynthesizer()
  @ObservationIgnored private let speechDelegate = SpeechCompletionDelegate()
  @ObservationIgnored private let audioDelegate = AudioCompletionDelegate()
  /// Retain every playback object until its own completion callback arrives. The order
  /// lets an ask bind to exactly the most recently-started playback, not whichever one
  /// happens to finish next.
  @ObservationIgnored private var playbackIDs: [ObjectIdentifier] = []
  @ObservationIgnored private var speechUtterances: [ObjectIdentifier: AVSpeechUtterance] = [:]
  @ObservationIgnored private var audioPlayers: [ObjectIdentifier: AVAudioPlayer] = [:]

  /// Public for the one-line SwiftUI status hookup documented in INTEGRATION.md.
  @ObservationIgnored lazy var listener = QuestionListener { [weak self] json in
    self?.sendRaw(json)
  }

  @ObservationIgnored private var task: URLSessionWebSocketTask?
  /// `var` since A19: `configure(host:port:)` can move the Mac out from under us.
  @ObservationIgnored private(set) var url: URL
  @ObservationIgnored private let path: String

  /// The user's *intent*, not the socket's state. `connected` answers "is the pipe up";
  /// this answers "should it be". Everything self-healing hangs off the difference.
  @ObservationIgnored private var wantConnected = false
  /// Exactly one pending reconnect, ever. Two overlapping timers is how backoff turns
  /// into a retry storm that hammers the Mac at the worst possible moment.
  @ObservationIgnored private var reconnectTask: Task<Void, Never>?
  @ObservationIgnored private var reconnectAttempt = 0
  @ObservationIgnored private var keepaliveTask: Task<Void, Never>?
  /// Bumped on every socket teardown. URLSession completion handlers from a socket we
  /// already gave up on arrive *after* the replacement is live; without this they would
  /// mark the healthy new socket dead and schedule a second reconnect on top of it.
  @ObservationIgnored private var generation = 0

  /// Dial the Mac's LAN IP. Never `localhost` — on the phone that resolves to the phone.
  ///
  /// These are the *fallback*, used only until someone calls `configure(host:port:)`
  /// once on the device; after that UserDefaults wins. `path` differs by target: the A11
  /// echo server (tools/echo_server.py, port 8765) accepts any path, but the real
  /// ingest server hard-codes `/ws/glasses` in `ingest.INGEST_PATH` and a bare `/`
  /// 404s at the handshake.
  static let defaultHost = "10.135.100.6"   // Rishi's Mac. `ipconfig getifaddr en0` to change.
  static let defaultPort = 8010             // ingest (t0 --port 8010); 8765 = A11 echo server

  /// UserDefaults keys. Read in `init`, written by `configure(host:port:)`.
  static let hostKey = "macHost"
  static let portKey = "macPort"

  /// The Mac drops sockets idle for 30 s. 10 s leaves room for two lost pings before
  /// that timer fires, which matters on venue Wi-Fi where one round trip is cheap to lose.
  static let keepaliveInterval: TimeInterval = 10
  /// 1, 2, 4, 8, then flat. Capped because a demo is minutes long: a 64 s hole between
  /// attempts is indistinguishable from "it never came back".
  static let maxBackoff: TimeInterval = 10

  init(
    host: String = MacLink.defaultHost,
    port: Int = MacLink.defaultPort,
    path: String = "/ws/glasses"
  ) {
    // A stored host outranks the compiled-in one: whoever typed it into the app last
    // was standing in the room. `integer(forKey:)` returns 0 when the key is unset,
    // which is also not a legal port, so one test covers both cases.
    let defaults = UserDefaults.standard
    let storedHost = defaults.string(forKey: MacLink.hostKey)
    let storedPort = defaults.integer(forKey: MacLink.portKey)
    let resolvedHost = (storedHost?.isEmpty == false) ? storedHost! : host
    let resolvedPort = storedPort > 0 ? storedPort : port

    self.path = path
    self.host = resolvedHost
    self.port = resolvedPort
    // Force-unwrap only on the compiled-in fallback, which is known-good; a garbage
    // string in UserDefaults must not be able to crash the app at launch.
    url =
      URL(string: "ws://\(resolvedHost):\(resolvedPort)\(path)")
      ?? URL(string: "ws://\(MacLink.defaultHost):\(MacLink.defaultPort)\(path)")!
    speechDelegate.didFinish = { [weak self] utterance in
      Task { @MainActor in self?.playbackFinished(utterance) }
    }
    audioDelegate.didFinish = { [weak self] player in
      Task { @MainActor in self?.playbackFinished(player) }
    }
    synth.delegate = speechDelegate
  }

  // MARK: - Intent

  /// Declare that we want to be connected, and stay connected, until `disconnect()`.
  func connect() {
    wantConnected = true
    reconnectAttempt = 0
    cancelReconnect()
    openSocket()
  }

  /// Withdraw the intent. This is the only thing that stops the reconnect loop — a
  /// failure never does, which is the whole point.
  func disconnect() {
    wantConnected = false
    reconnectAttempt = 0
    cancelReconnect()
    listener.cancel()
    teardownSocket()
    connected = false
    status = "not connected"
  }

  /// Point the link at a different Mac at runtime and remember the choice (A19).
  ///
  /// Venue Wi-Fi hands out a new subnet and the hard-coded `defaultHost` is suddenly
  /// wrong; without this the fix is an Xcode rebuild on a machine that may not be in
  /// the room. Reconnects immediately if we are supposed to be connected, so the only
  /// gesture needed is "type the IP, tap Apply".
  func configure(host newHost: String, port newPort: Int) {
    let trimmed = newHost.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty, newPort > 0, newPort < 65536,
      let candidate = URL(string: "ws://\(trimmed):\(newPort)\(path)")
    else {
      // Do not touch `status`: a bad value typed into the settings field must not read
      // as a dropped socket to CapturePacketSender.apply(macLinkStatus:) on a link that
      // is perfectly healthy. Surface it on its own property instead.
      configError = "bad host/port"
      return
    }
    configError = nil

    host = trimmed
    port = newPort
    url = candidate
    UserDefaults.standard.set(trimmed, forKey: Self.hostKey)
    UserDefaults.standard.set(newPort, forKey: Self.portKey)

    // A new address is a fresh start: the old backoff was counting failures against a
    // machine we are no longer talking to.
    reconnectAttempt = 0
    cancelReconnect()
    if wantConnected {
      openSocket()
    } else {
      teardownSocket()
      connected = false
      status = "not connected · \(trimmed):\(newPort)"
    }
  }

  // MARK: - Socket lifecycle

  /// Open a socket and prove it with a hello. Never call this directly from the UI —
  /// `connect()` owns the intent flag, this owns only the plumbing.
  private func openSocket() {
    teardownSocket()
    // A replaced socket is not a connected one until its hello is confirmed — otherwise
    // `configure` against an unreachable Mac reports "connected" until the new one fails.
    connected = false
    generation &+= 1
    let gen = generation

    let t = URLSession.shared.webSocketTask(with: url)
    task = t
    t.resume()
    status = "not connected · connecting \(host):\(port)"
    receive(gen: gen)
    sendHello(gen: gen)
  }

  /// Drop the current socket without touching `wantConnected`, `connected` or `status`.
  private func teardownSocket() {
    stopKeepalive()
    listener.cancel()
    generation &+= 1
    task?.cancel(with: .goingAway, reason: nil)
    task = nil
  }

  /// A11's echo hello, now doing double duty as the connection proof: `connected` and
  /// the backoff reset both hang off its completion, because a confirmed byte out is
  /// the earliest honest evidence the Mac is actually there.
  private func sendHello(gen: Int) {
    guard let task else { return }
    let body: [String: Any] = ["v": 1, "type": "echo", "text": "hello from the phone"]
    guard let data = try? JSONSerialization.data(withJSONObject: body),
      let payload = String(data: data, encoding: .utf8)
    else { return }

    task.send(.string(payload)) { [weak self] error in
      Task { @MainActor in
        guard let self, gen == self.generation else { return }
        if let error {
          self.fail("closed: \(error.localizedDescription)", gen: gen)
        } else {
          self.connected = true
          self.sentCount += 1
          // Reset here, not at `openSocket()`: resuming a task proves nothing, so a
          // flapping link would otherwise retry every 1 s forever.
          self.reconnectAttempt = 0
          self.status = "connected \(self.host):\(self.port)"
          self.startKeepalive(gen: gen)
        }
      }
    }
  }

  func send(_ text: String) {
    guard let task else {
      noSocket()
      return
    }
    let body: [String: Any] = ["v": 1, "type": "echo", "text": text]
    guard let data = try? JSONSerialization.data(withJSONObject: body),
      let payload = String(data: data, encoding: .utf8)
    else { return }

    let gen = generation
    task.send(.string(payload)) { [weak self] error in
      Task { @MainActor in
        guard let self else { return }
        if let error {
          self.fail("send failed: \(error.localizedDescription)", gen: gen)
        } else {
          guard gen == self.generation else { return }
          self.sentCount += 1
          self.status = "sent \(self.sentCount)"
        }
      }
    }
  }

  /// A14 — send a pre-built `wire` message verbatim, with no envelope of its own.
  /// `send(_:)` wraps its argument in {"type":"echo","text":...}, so a capture packet
  /// pushed through it would arrive as an echo carrying JSON as a string and never
  /// decode — the Mac would count it as `malformed`, not `received`.
  /// `completion` is the delivery report CapturePacketSender requires: called exactly
  /// once, `nil` on success. URLSession fires it on a background queue, hence @Sendable.
  func sendRaw(_ json: String, completion: @escaping @Sendable (Error?) -> Void = { _ in }) {
    guard let task else {
      noSocket()
      completion(URLError(.notConnectedToInternet))
      return
    }
    let gen = generation
    task.send(.string(json)) { [weak self] error in
      completion(error)
      Task { @MainActor in
        guard let self else { return }
        if let error {
          self.fail("send failed: \(error.localizedDescription)", gen: gen)
        } else {
          guard gen == self.generation else { return }
          self.sentCount += 1
        }
      }
    }
  }

  /// `receive` is ONE-SHOT. Re-arming it is the classic bug here: the first message
  /// from the Mac arrives and every one after it is silently dropped.
  private func receive(gen: Int) {
    task?.receive { [weak self] result in
      Task { @MainActor in
        // A callback from a socket we already replaced must not re-arm on the new one
        // (two receive chains, every message handled twice) nor kill it.
        guard let self, gen == self.generation else { return }
        switch result {
        case .success(let message):
          if case .string(let text) = message {
            print("from Mac: \(text)")
            self.handle(text)
          }
          self.receive(gen: gen)
        case .failure(let error):
          print("socket closed: \(error)")
          self.fail("closed: \(error.localizedDescription)", gen: gen)
        }
      }
    }
  }

  // MARK: - Self-healing

  /// One funnel for every way the socket can die. Stale callbacks are dropped here, so
  /// no caller has to think about generations beyond passing the one it captured.
  private func fail(_ reason: String, gen: Int) {
    guard gen == generation else { return }
    print("MacLink failure: \(reason)")
    connected = false
    listener.cancel()
    status = reason
    teardownSocket()
    scheduleReconnect()
  }

  /// A send attempted with no socket at all. Not a transport failure — there is nothing
  /// to tear down — but it is evidence the link is down, so it still pokes the retry.
  private func noSocket() {
    connected = false
    // Don't stomp a live countdown; the pending reconnect's status is more informative.
    if reconnectTask == nil { status = "not connected" }
    if wantConnected { scheduleReconnect() }
  }

  /// Exponential backoff: 1, 2, 4, 8, then flat at `maxBackoff`, plus a little jitter so
  /// a phone and any other client that dropped at the same instant don't re-dial in
  /// lockstep. `Task.sleep`, not DispatchQueue, so `cancelReconnect()` is instant and
  /// the whole thing stays on the main actor with no hop to reason about.
  private func scheduleReconnect() {
    guard wantConnected else { return }
    // The single-pending-attempt guard. A receive failure and a send failure routinely
    // land within microseconds of each other for the same drop.
    guard reconnectTask == nil else { return }

    reconnectAttempt += 1
    let attempt = reconnectAttempt
    let base = min(Self.maxBackoff, pow(2.0, Double(attempt - 1)))
    let delay = base + Double.random(in: 0...0.3)
    // Keeps the "closed" prefix CapturePacketSender.apply(macLinkStatus:) matches on.
    status = "closed · reconnecting in \(Int(base))s (attempt \(attempt))"

    reconnectTask = Task { @MainActor [weak self] in
      try? await Task.sleep(for: .seconds(delay))
      guard !Task.isCancelled, let self else { return }
      self.reconnectTask = nil
      guard self.wantConnected else { return }
      self.openSocket()
    }
  }

  private func cancelReconnect() {
    reconnectTask?.cancel()
    reconnectTask = nil
  }

  /// `{"v":1,"type":"ping"}` every 10 s. The Mac accepts it quietly and, more to the
  /// point, stops closing us for being idle — which is the normal state of this socket
  /// between "Connect to Mac" and the first glasses frame.
  private func startKeepalive(gen: Int) {
    stopKeepalive()
    keepaliveTask = Task { @MainActor [weak self] in
      while !Task.isCancelled {
        try? await Task.sleep(for: .seconds(MacLink.keepaliveInterval))
        guard !Task.isCancelled, let self, self.generation == gen else { return }
        self.sendPing(gen: gen)
      }
    }
  }

  private func stopKeepalive() {
    keepaliveTask?.cancel()
    keepaliveTask = nil
  }

  /// Deliberately does not move `sentCount` or `status`: that counter is compared
  /// against the Mac's `received` during the demo, and keepalives would inflate it.
  private func sendPing(gen: Int) {
    guard let task, gen == generation else { return }
    task.send(.string("{\"v\":1,\"type\":\"ping\"}")) { [weak self] error in
      guard let error else { return }
      Task { @MainActor in
        self?.fail("closed: \(error.localizedDescription)", gen: gen)
      }
    }
  }

  // MARK: - A16 · speak

  /// Route all Mac -> phone messages. `ask` follows its question audio on the wire;
  /// completion tracking below preserves that order at the hardware boundary too.
  private func handle(_ raw: String) {
    guard let data = raw.data(using: .utf8),
      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else {
      lastFromMac = raw
      return
    }
    let type = obj["type"] as? String ?? "?"
    let wireAudio = "audio"  // longevity.wire.AUDIO
    let text = obj["text"] as? String ?? ""
    lastFromMac = "\(type): \(text)"
    if type == "speak", !text.isEmpty { speak(text) }
    // A18 — pre-rendered ElevenLabs audio. The Mac falls back to a `speak` message
    // when synthesis fails, so both paths stay live and neither blocks the other.
    if type == wireAudio { playAudio(obj) }
    if type == "ask" {
      let awaitedPlaybackID = playbackIDs.last
      let connectionGeneration = generation
      let listenS = obj["listen_s"] as? Double ?? 8
      listener.receive(obj, afterPlayback: { [weak self] in
        guard let self else { return false }
        return await self.waitForPlayback(
          id: awaitedPlaybackID, timeout: listenS + 10)
      }, send: { [weak self] json in
        guard let self else { return }
        guard self.generation == connectionGeneration else {
          print("dropping answer from stale connection generation \(connectionGeneration)")
          return
        }
        self.sendRaw(json)
      })
    }
  }

  private func playbackStarted(_ utterance: AVSpeechUtterance) {
    let id = ObjectIdentifier(utterance)
    speechUtterances[id] = utterance
    playbackIDs.append(id)
  }

  private func playbackStarted(_ player: AVAudioPlayer) {
    let id = ObjectIdentifier(player)
    audioPlayers[id] = player
    playbackIDs.append(id)
  }

  private func playbackFinished(_ utterance: AVSpeechUtterance) {
    playbackFinished(id: ObjectIdentifier(utterance))
  }

  private func playbackFinished(_ player: AVAudioPlayer) {
    playbackFinished(id: ObjectIdentifier(player))
  }

  private func playbackFinished(id: ObjectIdentifier) {
    speechUtterances[id] = nil
    audioPlayers[id] = nil
    playbackIDs.removeAll { $0 == id }
  }

  private func waitForPlayback(id: ObjectIdentifier?, timeout: TimeInterval) async -> Bool {
    guard let id else {
      try? await Task.sleep(for: .milliseconds(300))
      return !Task.isCancelled
    }
    let deadline = Date().addingTimeInterval(timeout)
    while playbackIDs.contains(id) && Date() < deadline {
      try? await Task.sleep(for: .milliseconds(50))
      if Task.isCancelled { return false }
    }
    return !playbackIDs.contains(id)
  }

  /// Play pre-rendered audio pushed by the Mac (A18, wire.audio_message).
  private func playAudio(_ obj: [String: Any]) {
    guard obj["format"] as? String == "mp3",
      let encoded = obj["data"] as? String,
      let data = Data(base64Encoded: encoded)
    else {
      status = "audio message malformed"
      return
    }
    do {
      let session = AVAudioSession.sharedInstance()
      try session.setCategory(.playback, options: [.allowBluetoothA2DP])
      try session.setActive(true)
    } catch {
      // Same -50 trap as speak(): never return here, the existing route is usually fine.
      print("audio session (non-fatal): \(error)")
    }
    do {
      let player = try AVAudioPlayer(data: data)
      player.delegate = audioDelegate
      player.prepareToPlay()
      playbackStarted(player)
      guard player.play() else {
        playbackFinished(player)
        status = "audio playback failed"
        return
      }
      spokenCount += 1
      status = "played #\(spokenCount) · \(data.count / 1024) KB mp3"
    } catch {
      status = "audio playback failed"
      print("audio playback failed: \(error)")
    }
  }

  /// hardware_software.md §19 — the exact session config validated on real hardware.
  /// Without `.allowBluetoothA2DP` the speech comes out of the phone, not the glasses.
  /// §29 confirmed this works *concurrently* with a live DAT camera stream.
  ///
  /// Do not add microphone input here: §22 warns that opening a Bluetooth mic can make
  /// iOS drop off A2DP and degrade this exact output path.
  private func speak(_ text: String) {
    // Configure, but never bail on failure. The app may already hold a correctly
    // configured session — in which case this redundant setCategory throws
    // OSStatus -50 (kAudio_ParamError) and the existing route is fine anyway. The
    // sample's own validated speakTest() ignores this error and speaks regardless;
    // returning early here is what silently swallowed A16's first attempts.
    do {
      let session = AVAudioSession.sharedInstance()
      try session.setCategory(.playback, mode: .spokenAudio, options: [.allowBluetoothA2DP])
      try session.setActive(true)
    } catch {
      print("audio session (non-fatal): \(error)")
    }

    let utterance = AVSpeechUtterance(string: text)
    utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
    utterance.rate = 0.48  // matches the validated speakTest()
    playbackStarted(utterance)
    synth.speak(utterance)
    spokenCount += 1
    status = "spoke #\(spokenCount): \(text.prefix(28))"

    if let out = AVAudioSession.sharedInstance().currentRoute.outputs.first {
      print("speaking out: \(out.portName) [\(out.portType.rawValue)]")
    }
  }
}
