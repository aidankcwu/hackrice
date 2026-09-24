//  MacLink.swift
//
//  ios/Brian addition (app/native, N-001), additive only: `lastSpokenText` and
//  `lastSpokenAt` observables, set in `handle(_:)` whenever the Mac sends a `speak` or
//  `audio` message, so the Connect screen can show what Bryan last said. Nothing else
//  in this file changed.
//
//  docs/PERSON_A.md A11 — prove the phone <-> Mac socket before any payload rides on it.
//
//  Kept deliberately small. A11 exists as its own task because without
//  NSLocalNetworkUsageDescription and NSAppTransportSecurity -> NSAllowsLocalNetworking
//  in Info.plist, URLSessionWebSocketTask fails *silently* in a way that reads exactly
//  like a Python bug — and you lose an hour debugging the wrong half of the system.
//
//  One URLSessionWebSocketDelegate, for one job: reading the server's close code. A dead
//  socket still announces itself as a receive or send failure, and that is still the
//  reconnect signal. But a *refused* socket (the hosted backend closes with 4401 on a
//  bad token) must not be retried, and the close code is the only place that difference
//  is written down. Everything else still runs off send/receive completions.
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
//  3. **The server is no longer on the same Wi-Fi.** TestFlight testers dial a hosted
//     backend over TLS with one pasted URL, `wss://DOMAIN/t/NAME/ws/glasses?token=…`,
//     persisted under `serverURL`. When set it wins over host/port; clearing it falls
//     back to host/port for local dev. The token rides in the query *and* in an
//     `X-Access-Token` header. A server that closes with 4401 (or answers the handshake
//     with 401/403) has rejected the token: `status` says so and the reconnect loop
//     stops, because retrying a wrong token forever is a slow DoS on our own backend.
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

/// Reports the close code the *server* sent. `@unchecked Sendable` because URLSession
/// delegates must be Sendable and `didClose` is assigned exactly once, in `MacLink.init`,
/// before any socket exists. URLSession calls it on its own background queue.
private final class SocketCloseDelegate: NSObject, URLSessionWebSocketDelegate, @unchecked Sendable {
  var didClose: (@Sendable (_ taskID: Int, _ code: Int) -> Void)?
  func urlSession(
    _ session: URLSession, webSocketTask: URLSessionWebSocketTask,
    didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?
  ) {
    didClose?(webSocketTask.taskIdentifier, closeCode.rawValue)
  }
}

/// Where the socket dials and with what credential: either a pasted server URL (hosted
/// backend) or a LAN host/port (local dev). A pure value — no isolation, no I/O.
private struct ServerEndpoint: Sendable {
  let url: URL
  let host: String
  let port: Int
  /// `?token=` from a server URL, also sent as `X-Access-Token`. Nil in host/port mode.
  let token: String?
  /// Token-free, for status lines and the setup row. Never show or log `url` itself.
  let label: String

  /// `wss://DOMAIN/t/NAME/ws/glasses?token=…`. `https://`/`http://` are mapped to
  /// `wss://`/`ws://`, because a link pasted out of a chat app often arrives that way.
  /// An empty or `/` path gets `defaultPath`. Anything else is nil, never a crash.
  init?(serverURL raw: String, defaultPath: String) {
    let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty, var parts = URLComponents(string: trimmed),
      let scheme = parts.scheme?.lowercased()
    else { return nil }
    switch scheme {
    case "wss", "https": parts.scheme = "wss"
    case "ws", "http": parts.scheme = "ws"
    default: return nil
    }
    guard let host = parts.host, !host.isEmpty else { return nil }
    if parts.path.isEmpty || parts.path == "/" { parts.path = defaultPath }
    guard let url = parts.url else { return nil }
    let secure = parts.scheme == "wss"
    let token = parts.queryItems?.first(where: { $0.name == "token" })?.value
    self.url = url
    self.host = host
    self.port = parts.port ?? (secure ? 443 : 80)
    self.token = (token?.isEmpty == false) ? token : nil
    let portSuffix = parts.port.map { ":\($0)" } ?? ""
    self.label = "\(secure ? "wss" : "ws")://\(host)\(portSuffix)\(parts.path)"
  }

  /// The pre-TestFlight path: `ws://host:port/path`, no token.
  init?(host rawHost: String, port: Int, path: String) {
    let host = rawHost.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !host.isEmpty, port > 0, port < 65536,
      let url = URL(string: "ws://\(host):\(port)\(path)")
    else { return nil }
    self.url = url
    self.host = host
    self.port = port
    self.token = nil
    self.label = "\(host):\(port)"
  }
}

/// Calls a send completion at most once, from whichever of URLSession's callback and
/// `sendRaw`'s deadline gets there first. They race on different threads, hence the lock.
private final class OnceCompletion: @unchecked Sendable {
  private let lock = NSLock()
  private var completion: (@Sendable (Error?) -> Void)?

  init(_ completion: @escaping @Sendable (Error?) -> Void) {
    self.completion = completion
  }

  /// True when this call delivered the result; false when the other side already had.
  @discardableResult
  func fire(_ error: Error?) -> Bool {
    lock.lock()
    let pending = completion
    completion = nil
    lock.unlock()
    guard let pending else { return false }
    pending(error)
    return true
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
  /// What Bryan last said and when (N-001, additive). Set on every `speak`/`audio`
  /// message, whether it played through the glasses or went to a notification.
  var lastSpokenText: String?
  var lastSpokenAt: Date?

  /// The app-level route policy. `Link` sets this from the glasses state so a whisper
  /// is either played on connected glasses or posted as a notification, never both.
  @ObservationIgnored var shouldPlayWhispers: () -> Bool = { true }
  @ObservationIgnored var onWhisperNotification: ((String) -> Void)?
  /// Terminal token refusal hook. Link uses this to stop DAT and capture immediately.
  @ObservationIgnored var onAccessDenied: (() -> Void)?

  /// Where we are dialling right now. Observable so a settings field can show it.
  /// In server-URL mode these are the URL's host and port (443 for wss).
  private(set) var host: String
  private(set) var port: Int
  /// The pasted server URL, token included, or "" in host/port mode. Seed the
  /// "Server URL" field from this.
  private(set) var serverURL: String
  /// Token-free "where am I dialling" for the setup row: `wss://host/t/name/ws/glasses`
  /// or `10.0.0.5:8010`. Use this, not `serverURL`, anywhere a bystander can see it.
  private(set) var endpointLabel: String
  /// True once the server has refused our token (close 4401, or 401/403 at the
  /// handshake). Reconnecting is off until `configure(serverURL:)` or `connect()`.
  private(set) var accessDenied = false

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

  /// Own session instead of `URLSession.shared`, only so the close code reaches us.
  @ObservationIgnored private let socketDelegate = SocketCloseDelegate()
  @ObservationIgnored private lazy var session: URLSession = URLSession(
    configuration: .default, delegate: socketDelegate, delegateQueue: nil)
  @ObservationIgnored private var accessToken: String?
  /// Identifier of the most recently *opened* task. Unlike `task` it survives teardown,
  /// because the server's close frame can be reported after `fail` already tore down.
  @ObservationIgnored private var lastTaskID: Int?

  /// Dial the Mac's LAN IP. Never `localhost` — on the phone that resolves to the phone.
  ///
  /// These are the *fallback*, used only until someone calls `configure(host:port:)`
  /// once on the device; after that UserDefaults wins. `path` differs by target: the A11
  /// echo server (tools/echo_server.py, port 8765) accepts any path, but the real
  /// ingest server hard-codes `/ws/glasses` in `ingest.INGEST_PATH` and a bare `/`
  /// 404s at the handshake.
  static let defaultHost = "10.135.100.6"   // Rishi's Mac. `ipconfig getifaddr en0` to change.
  static let defaultPort = 8010             // ingest (t0 --port 8010); 8765 = A11 echo server

  /// UserDefaults keys. Read in `init`, written by the two `configure` methods.
  static let hostKey = "macHost"
  static let portKey = "macPort"
  /// The pasted `wss://…?token=…` URL. Present and parseable => it wins over host/port.
  // TODO(post-TestFlight): move the token out of UserDefaults into the Keychain; for now it is per-tester and revoked by removing the tester.
  static let serverURLKey = "serverURL"

  /// The backend's contract: a wrong token is closed with this code.
  static let tokenRejectedCloseCode = 4401
  static let accessTokenHeader = "X-Access-Token"

  /// The Mac drops sockets idle for 30 s. 10 s leaves room for two lost pings before
  /// that timer fires, which matters on venue Wi-Fi where one round trip is cheap to lose.
  static let keepaliveInterval: TimeInterval = 10
  /// 1, 2, 4, 8, then flat. Capped because a demo is minutes long: a 64 s hole between
  /// attempts is indistinguishable from "it never came back".
  static let maxBackoff: TimeInterval = 10
  /// A `sendRaw` that has not completed after this long means the socket is stalled,
  /// not slow. `URLSessionWebSocketTask.send` completes once the frame is handed to the
  /// TCP stack; on a healthy link that takes milliseconds even on cellular. When it
  /// hangs for seconds, the kernel send buffer is full because nothing is being ACKed
  /// (a dead cell handover, a captive portal, a half-open NAT mapping). TCP will not
  /// notice for minutes, and every packet sent in the meantime just queues behind it, so
  /// by the time it moves, those packets are stale (CLAUDE.md invariant 2). Three seconds
  /// is two capture intervals: long enough for one ~53 KB packet on a poor link, and
  /// short enough that a dead socket is replaced before the next question.
  nonisolated static let sendDeadline: TimeInterval = 3

  init(
    host: String = MacLink.defaultHost,
    port: Int = MacLink.defaultPort,
    path: String = "/ws/glasses"
  ) {
    // Precedence: a stored server URL (a tester's pasted wss:// link) beats a stored
    // host/port, which beats the compiled-in one. Whoever typed a value last knew more
    // than the build did. Garbage in UserDefaults falls through to the next tier.
    let storedURL = UserDefaults.standard.string(forKey: MacLink.serverURLKey) ?? ""
    let remote = ServerEndpoint(serverURL: storedURL, defaultPath: path)
    let endpoint = remote ?? MacLink.lanEndpoint(host: host, port: port, path: path)

    self.path = path
    self.serverURL = remote == nil ? "" : storedURL.trimmingCharacters(in: .whitespacesAndNewlines)
    self.host = endpoint.host
    self.port = endpoint.port
    self.endpointLabel = endpoint.label
    self.url = endpoint.url
    self.accessToken = endpoint.token
    socketDelegate.didClose = { [weak self] taskID, code in
      Task { @MainActor in self?.socketClosed(taskID: taskID, code: code) }
    }
    speechDelegate.didFinish = { [weak self] utterance in
      Task { @MainActor in self?.playbackFinished(utterance) }
    }
    audioDelegate.didFinish = { [weak self] player in
      Task { @MainActor in self?.playbackFinished(player) }
    }
    synth.delegate = speechDelegate
  }

  /// Host/port mode: stored `macHost`/`macPort` beat the arguments (a stored host was
  /// typed by someone standing in the room). `integer(forKey:)` returns 0 when unset,
  /// which is also not a legal port, so one test covers both. Force-unwrap only on the
  /// compiled-in fallback, which is known-good.
  private static func lanEndpoint(host: String, port: Int, path: String) -> ServerEndpoint {
    let defaults = UserDefaults.standard
    let storedHost = defaults.string(forKey: hostKey)
    let storedPort = defaults.integer(forKey: portKey)
    let resolvedHost = (storedHost?.isEmpty == false) ? storedHost! : host
    let resolvedPort = storedPort > 0 ? storedPort : port
    return ServerEndpoint(host: resolvedHost, port: resolvedPort, path: path)
      ?? ServerEndpoint(host: defaultHost, port: defaultPort, path: path)!
  }

  // MARK: - Intent

  /// Declare that we want to be connected, and stay connected, until `disconnect()`.
  ///
  /// Refuses until the user has agreed on `ConsentView` (ConsentView.swift). The gate is
  /// here rather than only on a button because every byte that leaves the phone —
  /// frames, answers — goes through this socket, whichever view wired it up.
  func connect() {
    guard StreamingConsent.isGranted else {
      status = "not connected · consent needed"
      return
    }
    accessDenied = false
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
  ///
  /// Choosing a LAN address is choosing LAN mode: this also clears any stored server
  /// URL, which would otherwise silently win again at the next launch.
  func configure(host newHost: String, port newPort: Int) {
    guard let next = ServerEndpoint(host: newHost, port: newPort, path: path) else {
      // Do not touch `status`: a bad value typed into the settings field must not read
      // as a dropped socket to CapturePacketSender.apply(macLinkStatus:) on a link that
      // is perfectly healthy. Surface it on its own property instead.
      configError = "bad host/port"
      return
    }
    configError = nil
    UserDefaults.standard.set(next.host, forKey: Self.hostKey)
    UserDefaults.standard.set(next.port, forKey: Self.portKey)
    UserDefaults.standard.removeObject(forKey: Self.serverURLKey)
    serverURL = ""
    retarget(next)
  }

  /// Point the link at a hosted backend and remember it. The whole onboarding for a
  /// TestFlight tester: paste `wss://DOMAIN/t/NAME/ws/glasses?token=…`, tap Apply.
  ///
  /// `ws://<mac-ip>:8010/ws/glasses` works too, for local dev. An empty string clears
  /// the stored URL and falls back to host/port. A value that does not parse is
  /// rejected onto `configError` and changes nothing, for the reason given in
  /// `configure(host:port:)`.
  func configure(serverURL raw: String) {
    let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    let next: ServerEndpoint
    if trimmed.isEmpty {
      UserDefaults.standard.removeObject(forKey: Self.serverURLKey)
      next = Self.lanEndpoint(host: Self.defaultHost, port: Self.defaultPort, path: path)
    } else if let parsed = ServerEndpoint(serverURL: trimmed, defaultPath: path) {
      UserDefaults.standard.set(trimmed, forKey: Self.serverURLKey)
      next = parsed
    } else {
      configError = "server URL must start with wss:// (or ws:// for a Mac on this Wi-Fi)"
      return
    }
    configError = nil
    serverURL = trimmed
    retarget(next)
  }

  /// Swap the endpoint and, if we were connected or trying to be, dial it now.
  private func retarget(_ next: ServerEndpoint) {
    host = next.host
    port = next.port
    url = next.url
    accessToken = next.token
    endpointLabel = next.label

    // A new address is a fresh start: the old backoff was counting failures against a
    // server we are no longer talking to. A token rejection was about the *old* URL, so
    // a new one is exactly the retry the rejection asked for.
    let retry = wantConnected || accessDenied
    accessDenied = false
    reconnectAttempt = 0
    cancelReconnect()
    if retry {
      connect()
    } else {
      teardownSocket()
      connected = false
      status = "not connected · \(next.label)"
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

    // The token rides in the query (it is part of `url`) *and* in this header. Our
    // backend accepts either; sending both survives a proxy that strips one of them.
    var request = URLRequest(url: url)
    if let accessToken {
      request.setValue(accessToken, forHTTPHeaderField: Self.accessTokenHeader)
    }
    let t = session.webSocketTask(with: request)
    task = t
    lastTaskID = t.taskIdentifier
    t.resume()
    status = "not connected · connecting \(endpointLabel)"
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
          self.status = "connected \(self.endpointLabel)"
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
  ///
  /// `deadline` (default `sendDeadline`, 3 s): if URLSession has not completed the send
  /// by then, `completion` gets `URLError(.timedOut)` and the socket is torn down and
  /// re-dialled through the normal backoff (`fail`). A stalled socket is treated as a
  /// dead one because it acts like one, except that it keeps accepting sends and queueing
  /// them. The real completion that arrives later, usually a cancellation, is swallowed,
  /// so the caller still hears back exactly once. Pass `nil` to wait indefinitely.
  func sendRaw(
    _ json: String,
    deadline: TimeInterval? = MacLink.sendDeadline,
    completion: @escaping @Sendable (Error?) -> Void = { _ in }
  ) {
    guard let task else {
      noSocket()
      completion(URLError(.notConnectedToInternet))
      return
    }
    let gen = generation
    let once = OnceCompletion(completion)
    task.send(.string(json)) { [weak self] error in
      // False when the deadline already reported this send and failed the socket.
      let reported = once.fire(error)
      Task { @MainActor in
        guard let self, reported else { return }
        if let error {
          self.fail("send failed: \(error.localizedDescription)", gen: gen)
        } else {
          guard gen == self.generation else { return }
          self.sentCount += 1
        }
      }
    }
    guard let deadline else { return }
    Task { @MainActor [weak self] in
      try? await Task.sleep(for: .seconds(deadline))
      // Already completed (the normal case): nothing stalled.
      guard once.fire(URLError(.timedOut)) else { return }
      guard let self else { return }
      // "send failed" keeps the prefix `apply(macLinkStatus:)` reads as down.
      self.fail("send failed: stalled > \(Int(deadline))s", gen: gen)
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
          // localizedDescription only: the full error carries the URL, and the URL
          // carries the token.
          print("socket closed: \(error.localizedDescription)")
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
    // A refusal is not a drop. Read it off the task *before* teardown throws it away.
    if let task, Self.isTokenRejection(task) {
      rejectAccess()
      return
    }
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

  // MARK: - Token rejection

  /// 4401 is the backend's "wrong token". 401/403 at the handshake covers a server that
  /// refuses before upgrading (Starlette turns a close-before-accept into an HTTP 403)
  /// and a proxy doing the check itself. No LAN Mac ever answers either, so local dev
  /// cannot trip this by accident.
  private static func isTokenRejection(_ task: URLSessionWebSocketTask) -> Bool {
    if task.closeCode.rawValue == tokenRejectedCloseCode { return true }
    let httpStatus = (task.response as? HTTPURLResponse)?.statusCode ?? 0
    return httpStatus == 401 || httpStatus == 403
  }

  /// The delegate's view of a close. The receive failure for the same close may land
  /// first and schedule a reconnect; this cancels it. `lastTaskID` rather than
  /// `generation` because `fail` has usually torn the task down already.
  private func socketClosed(taskID: Int, code: Int) {
    guard code == Self.tokenRejectedCloseCode, taskID == lastTaskID, !accessDenied else { return }
    rejectAccess()
  }

  /// Stop, and say why. Withdraws the intent so nothing re-dials a server that has told
  /// us no; `configure(serverURL:)` with a new URL, or a manual `connect()`, retries.
  /// Status keeps the "not connected" prefix CapturePacketSender reads as down.
  private func rejectAccess() {
    print("MacLink: server rejected the access token")
    wantConnected = false
    accessDenied = true
    reconnectAttempt = 0
    cancelReconnect()
    teardownSocket()
    connected = false
    status = "not connected · invalid access token — re-paste the server URL"
    onAccessDenied?()
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
    if (type == "speak" || type == wireAudio), !text.isEmpty {
      lastSpokenText = text
      lastSpokenAt = Date()
    }
    if type == "speak", !text.isEmpty {
      if shouldPlayWhispers() { speak(text) } else { onWhisperNotification?(text) }
    }
    // A18 — pre-rendered ElevenLabs audio. The Mac falls back to a `speak` message
    // when synthesis fails, so both paths stay live and neither blocks the other.
    if type == wireAudio {
      if shouldPlayWhispers() {
        playAudio(obj)
      } else {
        onWhisperNotification?(text.isEmpty ? "Bryan has a whisper for you." : text)
      }
    }
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
