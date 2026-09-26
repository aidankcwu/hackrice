// ConnectRows.derive: every state each Connect row can show, and the clipboard link check.
import Foundation
import Testing
@testable import Brian

@MainActor
struct ConnectRowsTests {
    let now = Date(timeIntervalSince1970: 1_790_000_000)

    private func healthy() -> ConnectionInputs {
        ConnectionInputs(
            glasses: .connected,
            link: .reachable("glasses.example.com/t/alice"),
            watching: true,
            watchingSince: now.addingTimeInterval(-12 * 60),
            accessDenied: false,
            backendConnected: true,
            stats: StreamStats(framesSent: 480, framesPerSecond: 0.67, secondsSinceLastFrame: 1,
                               serverAcknowledged: true),
            now: now)
    }

    private func rows(_ input: ConnectionInputs, checking: Bool = false, registering: Bool = false) -> ConnectRows {
        ConnectRows.derive(input, checking: checking, registering: registering)
    }

    @Test func allGreenWhileWatching() {
        let r = rows(healthy())
        #expect(r.invite == ConnectRow(level: .green, text: "Server reachable · glasses.example.com/t/alice"))
        #expect(r.glasses == ConnectRow(level: .green, text: "Connected"))
        #expect(r.stream == ConnectRow(level: .green, text: "Watching 12\u{00A0}min · 0.7\u{00A0}frames/s · last frame 1\u{00A0}s\u{00A0}ago"))
    }

    @Test func nothingSet() {
        var input = healthy()
        input.link = .notSet
        input.glasses = .notRegistered
        input.watching = false
        let r = rows(input)
        #expect(r.invite == ConnectRow(level: .grey, text: "Paste the link from your invite"))
        #expect(r.glasses == ConnectRow(level: .grey, text: "Tap Register, then allow Zeroist in Meta AI"))
        #expect(r.stream == ConnectRow(level: .grey, text: "Not watching"))
    }

    @Test func inFlightStatesAreAmber() {
        let r = rows(healthy(), checking: true, registering: true)
        #expect(r.invite == ConnectRow(level: .amber, text: "Checking…"))
        #expect(r.glasses == ConnectRow(level: .amber, text: "Registering…"))
    }

    @Test func tokenRejectedNamesTheFix() {
        var input = healthy()
        input.link = .unreachable(APIError.tokenRejected.sentence)
        #expect(rows(input).invite == ConnectRow(level: .red, text: "Invalid invite link",
                                                 fix: APIError.tokenRejected.sentence))
        input = healthy()
        input.accessDenied = true
        #expect(rows(input).invite.level == .red)
    }

    @Test func unreachableCarriesItsSentence() {
        var input = healthy()
        input.link = .unreachable("Nothing answers at glasses.example.com/t/alice. Check the link from your invite.")
        #expect(rows(input).invite == ConnectRow(
            level: .red, text: "Server unreachable",
            fix: "Nothing answers at glasses.example.com/t/alice. Check the link from your invite."))
    }

    @Test func glassesOffIsRedWithFix() {
        var input = healthy()
        input.glasses = .unavailable
        #expect(rows(input).glasses == ConnectRow(level: .red, text: "Glasses off", fix: ConnectRows.glassesOffFix))
    }

    @Test func registeredGlassesConnectWhenWatching() {
        var input = healthy()
        input.glasses = .registered
        #expect(rows(input).glasses == ConnectRow(level: .amber, text: "Connecting…"))
        input.watching = false
        #expect(rows(input).glasses == ConnectRow(level: .green, text: "Registered"))
    }

    @Test func streamAmberStates() {
        var input = healthy()
        input.backendConnected = false
        #expect(rows(input).stream == ConnectRow(level: .amber, text: "Reconnecting…"))
        input = healthy()
        input.stats.secondsSinceLastFrame = nil
        #expect(rows(input).stream == ConnectRow(level: .amber, text: "Starting…"))
        input.stats.secondsSinceLastFrame = 25
        #expect(rows(input).stream == ConnectRow(level: .amber, text: "No frames for 25 s"))
    }

    @Test func startNeedsReachableLinkAndRegisteredGlasses() {
        var input = healthy()
        input.watching = false
        #expect(rows(input).canStart)
        input.glasses = .registered
        #expect(rows(input).canStart)
        input.glasses = .notRegistered
        #expect(!rows(input).canStart)
        input.glasses = .unavailable
        #expect(!rows(input).canStart)
        input = healthy()
        input.link = .notSet
        #expect(!rows(input).canStart)
        input.link = .unreachable(APIError.tokenRejected.sentence)
        #expect(!rows(input).canStart)
        input = healthy()
        input.accessDenied = true
        #expect(!rows(input).canStart)
    }

    @Test func inviteLinkDetection() {
        #expect(ServerURL.isInviteLink("wss://glasses.example.com/t/alice/ws/glasses?token=abc123"))
        #expect(ServerURL.isInviteLink("  wss://glasses.example.com/ws/glasses?token=abc\n"))
        #expect(!ServerURL.isInviteLink("ws://10.0.0.5:8010/ws/glasses"))
        #expect(!ServerURL.isInviteLink("wss://glasses.example.com/t/alice/ws/glasses"))
        #expect(!ServerURL.isInviteLink("wss://glasses.example.com/t/alice/ws/glasses?token="))
        #expect(ServerURL.isInviteLink("https://glasses.example.com/t/alice/ws/glasses?token=abc"))
        #expect(ServerURL.isInviteLink("Open https://glasses.example.com/t/alice/app?token=abc to begin"))
        #expect(ServerURL.isInviteLink("https://glasses.example.com/t/alice/dashboard/day?token=abc"))
        #expect(!ServerURL.isInviteLink("buy milk"))
    }
}
