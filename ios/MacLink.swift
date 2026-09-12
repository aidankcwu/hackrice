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
//
//  A14 replaces `send(_:)`'s payload with a real capture packet. The transport stays.

import AVFoundation
import Foundation
import Observation

@Observable
@MainActor
final class MacLink {
  /// True once the socket is resumed. A dead Mac shows up as a send/receive failure.
  var connected = false
  /// Human-readable state for the button label — this is a debugging tool.
  var status = "not connected"
  /// Most recent message the Mac pushed to us. Proves Mac -> phone.
  var lastFromMac = "—"
  var sentCount = 0
  /// Utterances spoken out the glasses. A16's proof.
  var spokenCount = 0

  @ObservationIgnored private let synth = AVSpeechSynthesizer()

  @ObservationIgnored private var task: URLSessionWebSocketTask?
  @ObservationIgnored let url: URL

  /// Dial the Mac's LAN IP. Never `localhost` — on the phone that resolves to the phone.
  ///
  /// EDIT THESE TWO when moving between machines. `path` differs by target: the A11
  /// echo server (tools/echo_server.py, port 8765) accepts any path, but the real
  /// ingest server hard-codes `/ws/glasses` in `ingest.INGEST_PATH` and a bare `/`
  /// 404s at the handshake.
  static let defaultHost = "10.136.156.29"   // Rishi's Mac. `ipconfig getifaddr en0` to change.
  static let defaultPort = 8010             // ingest (t0 --port 8010); 8765 = A11 echo server

  init(
    host: String = MacLink.defaultHost,
    port: Int = MacLink.defaultPort,
    path: String = "/ws/glasses"
  ) {
    url = URL(string: "ws://\(host):\(port)\(path)")!
  }

  func connect() {
    disconnect()
    let t = URLSession.shared.webSocketTask(with: url)
    task = t
    t.resume()
    connected = true
    status = "connected \(url.host ?? "?"):\(url.port ?? 0)"
    receive()
    send("hello from the phone")
  }

  func disconnect() {
    task?.cancel(with: .goingAway, reason: nil)
    task = nil
    connected = false
    status = "not connected"
  }

  func send(_ text: String) {
    guard let task else {
      status = "not connected"
      return
    }
    let body: [String: Any] = ["v": 1, "type": "echo", "text": text]
    guard let data = try? JSONSerialization.data(withJSONObject: body),
      let payload = String(data: data, encoding: .utf8)
    else { return }

    task.send(.string(payload)) { [weak self] error in
      Task { @MainActor in
        guard let self else { return }
        if let error {
          self.connected = false
          self.status = "send failed: \(error.localizedDescription)"
        } else {
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
  func sendRaw(_ json: String) {
    guard let task else {
      status = "not connected"
      return
    }
    task.send(.string(json)) { [weak self] error in
      Task { @MainActor in
        guard let self else { return }
        if let error {
          self.connected = false
          self.status = "send failed: \(error.localizedDescription)"
        } else {
          self.sentCount += 1
        }
      }
    }
  }

  /// `receive` is ONE-SHOT. Re-arming it is the classic bug here: the first message
  /// from the Mac arrives and every one after it is silently dropped.
  private func receive() {
    task?.receive { [weak self] result in
      Task { @MainActor in
        guard let self else { return }
        switch result {
        case .success(let message):
          if case .string(let text) = message {
            print("from Mac: \(text)")
            self.handle(text)
          }
          self.receive()
        case .failure(let error):
          print("socket closed: \(error)")
          self.connected = false
          self.status = "closed: \(error.localizedDescription)"
        }
      }
    }
  }

  // MARK: - A16 · speak

  /// Route an inbound message by `type`. A14 adds nothing here; the Mac only ever
  /// pushes `speak` (A16) and later `audio` (A18) down this socket.
  private func handle(_ raw: String) {
    guard let data = raw.data(using: .utf8),
      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else {
      lastFromMac = raw
      return
    }
    let type = obj["type"] as? String ?? "?"
    let text = obj["text"] as? String ?? ""
    lastFromMac = "\(type): \(text)"
    if type == "speak", !text.isEmpty { speak(text) }
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
    synth.speak(utterance)
    spokenCount += 1
    status = "spoke #\(spokenCount): \(text.prefix(28))"

    if let out = AVAudioSession.sharedInstance().currentRoute.outputs.first {
      print("speaking out: \(out.portName) [\(out.portType.rawValue)]")
    }
  }
}
