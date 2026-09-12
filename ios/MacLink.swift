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

  @ObservationIgnored private var task: URLSessionWebSocketTask?
  @ObservationIgnored let url: URL

  /// Dial the Mac's LAN IP. Never `localhost` — on the phone that resolves to the phone.
  init(host: String = "10.135.100.7", port: Int = 8765) {
    url = URL(string: "ws://\(host):\(port)/")!
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
            self.lastFromMac = text
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
}
