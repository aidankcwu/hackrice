// One derived answer to "is it working?" (APP_PRD.md "Status pill", "Connect screen").
// AppState computes both values from its inputs; the derivation is a pure function so
// every state can be tested without a socket, glasses or a clock.
import Foundation

enum ConnectionLevel: Equatable {
    case grey       // nothing wrong, nothing running
    case amber      // on its way: starting, reconnecting, waiting for frames
    case green      // watching and frames are reaching the server
    case red        // needs the person: the sentence says what to fix
}

struct ConnectionStatus: Equatable {
    let level: ConnectionLevel
    /// One line for the pill, noun + state: "Watching · 12 min", "Glasses off".
    let text: String
}

/// Live numbers for the Connect screen's panel. All per watching session.
struct StreamStats: Equatable {
    var framesSent: Int = 0
    var framesPerSecond: Double = 0
    /// Nil until the first frame of this session is delivered.
    var secondsSinceLastFrame: TimeInterval? = nil
    /// The socket's hello was confirmed and at least one frame of this session was
    /// delivered. The server sends no per-frame ack, so this is the strongest signal
    /// the phone has that the server is taking what it sends.
    var serverAcknowledged: Bool = false
    var lastSpokenText: String? = nil
    var lastSpokenAt: Date? = nil
}

/// Everything the status depends on, snapshotted so the derivation is testable.
struct ConnectionInputs {
    var glasses: GlassesState
    var link: LinkState
    var watching: Bool
    var watchingSince: Date?
    /// The server refused the token (MacLink close 4401, or 401/403 from the API).
    var accessDenied: Bool
    /// MacLink's socket is up and its hello was confirmed.
    var backendConnected: Bool
    var stats: StreamStats
    var now: Date
}

extension ConnectionStatus {
    /// Frames arrive every 1.5 s; this long without one while connected means the
    /// glasses stopped sending even though nothing reported an error.
    static let staleFrameSeconds: TimeInterval = 10

    /// Order matters: the first problem a person has to fix wins.
    static func derive(_ input: ConnectionInputs) -> ConnectionStatus {
        if input.accessDenied || input.link.isTokenRejected {
            return ConnectionStatus(level: .red, text: "Invalid invite link")
        }
        switch input.link {
        case .notSet:
            return ConnectionStatus(level: .red, text: "Not connected")
        case .unreachable:
            return ConnectionStatus(level: .red, text: "Server unreachable")
        case .reachable:
            break
        }
        if input.glasses == .unavailable || input.glasses == .notRegistered {
            return ConnectionStatus(level: .red, text: "Glasses off")
        }
        guard input.watching else {
            return ConnectionStatus(level: .grey, text: "Not watching")
        }
        guard input.backendConnected else {
            return ConnectionStatus(level: .amber, text: "Reconnecting…")
        }
        guard let sinceLast = input.stats.secondsSinceLastFrame else {
            return ConnectionStatus(level: .amber, text: "Starting…")
        }
        if sinceLast > staleFrameSeconds {
            return ConnectionStatus(level: .amber, text: "No frames for \(Int(sinceLast)) s")
        }
        let minutes = input.watchingSince.map { max(0, Int(input.now.timeIntervalSince($0) / 60)) } ?? 0
        return ConnectionStatus(level: .green, text: "Watching · \(minutes) min")
    }
}

extension LinkState {
    /// `testServer` and `checkAccess` both land a refused token here as this sentence.
    var isTokenRejected: Bool {
        if case .unreachable(let sentence) = self { return sentence == APIError.tokenRejected.sentence }
        return false
    }
}
