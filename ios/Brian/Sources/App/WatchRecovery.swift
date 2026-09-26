// DEFECT 1 (debug task): the glasses leave `.connected` for many harmless reasons while
// watching -- a lock-button press, a glance at another app, a brief DAT flicker, a
// touchpad pause -- and none of them should end the session or need a re-tap. This is the
// pure timing decision AppState drives every tick: given how long the glasses have been
// down and whether the phone can currently see and drive DAT, say what to do next.
// AppState owns the clock and performs the actual restart or end; this type only answers
// the question, so every case is testable without hardware, Task or Date.
import Foundation

enum WatchRecovery {
    enum Action: Equatable {
        /// Keep the transport up and check again shortly: still inside the grace period,
        /// the app is backgrounded, or the one restart already ran and there is still
        /// budget left before giving up.
        case wait
        /// Try the one automatic `session.startStream()` retry for this drop.
        case restart
        /// Give up: end the watch.
        case end
    }

    /// The glasses may reconnect by themselves for this long, once the app is active,
    /// before anything else happens.
    static let graceSeconds: TimeInterval = 5
    /// Total active-time budget, from the drop, before ending the watch outright. Chosen
    /// as the grace period plus `GlassesSession.startDeadline` (20 s): by the time this
    /// elapses the one restart attempt has always already resolved, one way or the other.
    static let totalSeconds: TimeInterval = 25

    /// - Parameters:
    ///   - watching: `AppState.watching`; recovery only matters mid-watch.
    ///   - glasses: the glasses' current `GlassesState`.
    ///   - appActive: false while backgrounded (RootView's `scenePhase`). Recovery never
    ///     restarts or ends while the app cannot see or drive DAT (DEFECT 1.b).
    ///   - secondsSinceDropped: time since the glasses left `.connected`, counted only
    ///     while the app has been active -- AppState freezes this while backgrounded, so
    ///     a long spell locked never itself burns through the budget.
    ///   - restartAttempted: whether the one automatic restart for this drop already ran.
    static func action(watching: Bool, glasses: GlassesState, appActive: Bool,
                       secondsSinceDropped: TimeInterval, restartAttempted: Bool) -> Action {
        guard watching, glasses != .connected else { return .wait }
        guard appActive else { return .wait }
        guard secondsSinceDropped >= graceSeconds else { return .wait }
        guard restartAttempted else { return .restart }
        guard secondsSinceDropped >= totalSeconds else { return .wait }
        return .end
    }
}
