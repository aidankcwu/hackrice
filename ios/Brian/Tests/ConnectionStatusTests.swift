// ConnectionStatus.derive covers every state the pill can show; StreamStats per session.
import Foundation
import Testing
@testable import Brian

@MainActor
struct ConnectionStatusTests {
    let now = Date(timeIntervalSince1970: 1_790_000_000)

    /// A healthy watching session: link up, glasses connected, a frame 1 s ago.
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

    @Test func noLink() {
        var input = healthy()
        input.link = .notSet
        input.watching = false
        #expect(ConnectionStatus.derive(input) == ConnectionStatus(level: .red, text: "Not connected"))
    }

    @Test func linkUnreachable() {
        var input = healthy()
        input.link = .unreachable("Nothing answers at glasses.example.com/t/alice. Check the link from your invite.")
        #expect(ConnectionStatus.derive(input) == ConnectionStatus(level: .red, text: "Server unreachable"))
    }

    @Test func glassesOff() {
        for state in [GlassesState.unavailable, .notRegistered] {
            var input = healthy()
            input.glasses = state
            #expect(ConnectionStatus.derive(input) == ConnectionStatus(level: .red, text: "Glasses off"))
        }
    }

    @Test func watchingHealthy() {
        #expect(ConnectionStatus.derive(healthy()) == ConnectionStatus(level: .green, text: "Watching · 12 min"))
    }

    @Test func notWatching() {
        var input = healthy()
        input.watching = false
        input.watchingSince = nil
        #expect(ConnectionStatus.derive(input) == ConnectionStatus(level: .grey, text: "Not watching"))
        input.glasses = .registered       // no DAT session yet; Start watching opens one
        #expect(ConnectionStatus.derive(input).level == .grey)
    }

    @Test func reconnecting() {
        var input = healthy()
        input.backendConnected = false
        #expect(ConnectionStatus.derive(input) == ConnectionStatus(level: .amber, text: "Reconnecting…"))
    }

    @Test func startingBeforeFirstFrame() {
        var input = healthy()
        input.stats = StreamStats()
        #expect(ConnectionStatus.derive(input) == ConnectionStatus(level: .amber, text: "Starting…"))
    }

    @Test func startInFlightIsAmberBeforeWatchingBegins() {
        var input = healthy()
        input.watching = false
        input.starting = true
        #expect(ConnectionStatus.derive(input) == ConnectionStatus(level: .amber, text: "Starting…"))
    }

    @Test func framesStopped() {
        var input = healthy()
        input.stats.secondsSinceLastFrame = 14
        #expect(ConnectionStatus.derive(input) == ConnectionStatus(level: .amber, text: "No frames for 14 s"))
    }

    @Test func tokenRejected() {
        var socket = healthy()
        socket.accessDenied = true
        socket.watching = false
        #expect(ConnectionStatus.derive(socket) == ConnectionStatus(level: .red, text: "Invalid invite link"))

        var api = healthy()
        api.link = .unreachable(APIError.tokenRejected.sentence)
        #expect(ConnectionStatus.derive(api) == ConnectionStatus(level: .red, text: "Invalid invite link"))
    }

    @Test func tokenRejectedOutranksGlassesOff() {
        var input = healthy()
        input.accessDenied = true
        input.glasses = .unavailable
        #expect(ConnectionStatus.derive(input).text == "Invalid invite link")
    }

    @Test func demoStatsTickAtTheSenderCadence() {
        let since = now.addingTimeInterval(-60)
        let stats = AppState.demoStats(watching: true, since: since, now: now)
        #expect(stats.framesSent == 40)
        #expect(abs(stats.framesPerSecond - 1 / 1.5) < 1e-9)
        #expect(stats.secondsSinceLastFrame.map { $0 < 1.5 } == true)
        #expect(stats.serverAcknowledged)
        #expect(stats.lastSpokenText == AppState.demoSpokenLine)

        let idle = AppState.demoStats(watching: false, since: nil, now: now)
        #expect(idle.framesSent == 0)
        #expect(!idle.serverAcknowledged)
        #expect(idle.secondsSinceLastFrame == nil)
    }

    @Test func demoAppStateIsGreen() {
        let state = AppState(demo: true)
        let status = state.connectionStatus
        #expect(status.level == .green)
        #expect(status.text == "Watching · 14 min")
        #expect(state.streamStats.framesSent > 0)
    }
}
