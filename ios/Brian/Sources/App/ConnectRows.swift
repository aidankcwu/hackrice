// The Connect screen's three rows (APP_PRD.md "Connect screen"): Invite link, Glasses,
// Stream. Each is a dot, one line of live state, and, when red, one sentence of fix.
// Pure, like ConnectionStatus.derive, so every row state is testable without hardware.
import Foundation

struct ConnectRow: Equatable {
    let level: ConnectionLevel
    /// One line of live state: "Server reachable · glasses.example.com/t/alice".
    let text: String
    /// Red rows only: what to do, one sentence, shown in `Brian.cost`.
    var fix: String? = nil
}

struct ConnectRows: Equatable {
    let invite: ConnectRow
    let glasses: ConnectRow
    let stream: ConnectRow
    /// "Start watching" is enabled only when the rows above are ready: a reachable invite
    /// link and glasses at least registered. While disabled the rows already say what is
    /// missing, so the button needs no message of its own. Stop is never disabled.
    let canStart: Bool

    /// `checking`: a link test is in flight. `registering`: DAT registration is running.
    static func derive(_ input: ConnectionInputs, checking: Bool, registering: Bool) -> ConnectRows {
        ConnectRows(
            invite: invite(input, checking: checking),
            glasses: glasses(input, registering: registering),
            stream: stream(input),
            canStart: canStart(input))
    }

    static func canStart(_ input: ConnectionInputs) -> Bool {
        guard case .reachable = input.link, !input.accessDenied else { return false }
        return input.glasses == .registered || input.glasses == .connected
    }

    static let glassesOffFix = "Turn the glasses on and check they are paired and connected in Meta AI, then come back here."

    static func invite(_ input: ConnectionInputs, checking: Bool) -> ConnectRow {
        if checking { return ConnectRow(level: .amber, text: "Checking…") }
        if input.accessDenied || input.link.isTokenRejected {
            return ConnectRow(level: .red, text: "Invalid invite link", fix: APIError.tokenRejected.sentence)
        }
        switch input.link {
        case .notSet:
            return ConnectRow(level: .grey, text: "Paste the link from your invite")
        case .unreachable(let sentence):
            return ConnectRow(level: .red, text: "Server unreachable", fix: sentence)
        case .reachable(let label):
            return ConnectRow(level: .green, text: "Server reachable · \(label)")
        }
    }

    static func glasses(_ input: ConnectionInputs, registering: Bool) -> ConnectRow {
        if registering { return ConnectRow(level: .amber, text: "Registering…") }
        switch input.glasses {
        case .unavailable:
            return ConnectRow(level: .red, text: "Glasses off", fix: glassesOffFix)
        case .notRegistered:
            return ConnectRow(level: .grey, text: "Open Meta AI and tap Allow")
        case .registered:
            // Registered glasses open their camera session when watching starts.
            return input.watching
                ? ConnectRow(level: .amber, text: "Connecting…")
                : ConnectRow(level: .green, text: "Registered")
        case .connected:
            return ConnectRow(level: .green, text: "Connected")
        }
    }

    static func stream(_ input: ConnectionInputs) -> ConnectRow {
        guard input.watching else { return ConnectRow(level: .grey, text: "Not watching") }
        guard input.backendConnected else { return ConnectRow(level: .amber, text: "Reconnecting…") }
        guard let sinceLast = input.stats.secondsSinceLastFrame else {
            return ConnectRow(level: .amber, text: "Starting…")
        }
        if sinceLast > ConnectionStatus.staleFrameSeconds {
            return ConnectRow(level: .amber, text: "No frames for \(Int(sinceLast)) s")
        }
        let minutes = input.watchingSince.map { max(0, Int(input.now.timeIntervalSince($0) / 60)) } ?? 0
        let rate = String(format: "%.1f", input.stats.framesPerSecond)
        // No-break spaces keep each number with its unit when the line wraps.
        return ConnectRow(level: .green,
                          text: "Watching \(minutes)\u{00A0}min · \(rate)\u{00A0}frames/s · last frame \(Int(sinceLast))\u{00A0}s\u{00A0}ago")
    }
}

extension ServerURL {
    /// A hosted invite link as testers receive it: wss://DOMAIN/…/ws/glasses?token=T.
    /// The clipboard offer on Connect only proposes strings that pass this.
    static func isInviteLink(_ raw: String) -> Bool {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.lowercased().hasPrefix("wss://"), let parsed = ServerURL(trimmed) else { return false }
        return parsed.token != nil && parsed.socketURL.path.hasSuffix(socketPath)
    }
}
