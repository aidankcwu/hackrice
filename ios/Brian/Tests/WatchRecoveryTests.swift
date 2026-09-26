// WatchRecovery.action: DEFECT 1's grace period, one automatic restart, then give up.
import Foundation
import Testing
@testable import Brian

struct WatchRecoveryTests {
    @Test func notWatchingOrAlreadyConnectedNeedsNothing() {
        #expect(WatchRecovery.action(watching: false, glasses: .registered, appActive: true,
                                     secondsSinceDropped: 999, restartAttempted: true) == .wait)
        #expect(WatchRecovery.action(watching: true, glasses: .connected, appActive: true,
                                     secondsSinceDropped: 999, restartAttempted: true) == .wait)
    }

    @Test func backgroundedNeverRestartsOrEnds() {
        #expect(WatchRecovery.action(watching: true, glasses: .registered, appActive: false,
                                     secondsSinceDropped: 0, restartAttempted: false) == .wait)
        #expect(WatchRecovery.action(watching: true, glasses: .registered, appActive: false,
                                     secondsSinceDropped: 999, restartAttempted: true) == .wait)
    }

    @Test func withinGraceJustWaits() {
        #expect(WatchRecovery.action(watching: true, glasses: .registered, appActive: true,
                                     secondsSinceDropped: 0, restartAttempted: false) == .wait)
        #expect(WatchRecovery.action(watching: true, glasses: .registered, appActive: true,
                                     secondsSinceDropped: WatchRecovery.graceSeconds - 0.1,
                                     restartAttempted: false) == .wait)
    }

    @Test func graceElapsedTriesTheOneRestart() {
        #expect(WatchRecovery.action(watching: true, glasses: .registered, appActive: true,
                                     secondsSinceDropped: WatchRecovery.graceSeconds,
                                     restartAttempted: false) == .restart)
    }

    @Test func neverGivesUpBeforeTheOneRestartRan() {
        // Even well past the total budget (e.g. a long spell in the background just
        // ended and the accumulated active time is already large), the restart still
        // gets its one try before anything gives up.
        #expect(WatchRecovery.action(watching: true, glasses: .registered, appActive: true,
                                     secondsSinceDropped: WatchRecovery.totalSeconds * 4,
                                     restartAttempted: false) == .restart)
    }

    @Test func afterRestartWaitsOutTheRemainingBudget() {
        #expect(WatchRecovery.action(watching: true, glasses: .registered, appActive: true,
                                     secondsSinceDropped: WatchRecovery.graceSeconds,
                                     restartAttempted: true) == .wait)
        #expect(WatchRecovery.action(watching: true, glasses: .registered, appActive: true,
                                     secondsSinceDropped: WatchRecovery.totalSeconds - 0.1,
                                     restartAttempted: true) == .wait)
    }

    @Test func totalBudgetElapsedGivesUp() {
        #expect(WatchRecovery.action(watching: true, glasses: .registered, appActive: true,
                                     secondsSinceDropped: WatchRecovery.totalSeconds,
                                     restartAttempted: true) == .end)
    }

    @Test func everyGlassesStateShortOfConnectedIsEligible() {
        for state: GlassesState in [.unavailable, .notRegistered, .registered] {
            #expect(WatchRecovery.action(watching: true, glasses: state, appActive: true,
                                         secondsSinceDropped: WatchRecovery.totalSeconds,
                                         restartAttempted: true) == .end, "\(state)")
        }
    }

    @Test func becomingInactiveAtAnyPointAlwaysWaits() {
        // A restart already attempted and past the total budget would otherwise end the
        // watch; appActive == false must override that unconditionally (DEFECT 1.b).
        #expect(WatchRecovery.action(watching: true, glasses: .unavailable, appActive: false,
                                     secondsSinceDropped: WatchRecovery.totalSeconds + 100,
                                     restartAttempted: true) == .wait)
    }
}
