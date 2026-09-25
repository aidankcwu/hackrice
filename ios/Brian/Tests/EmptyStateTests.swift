// D-009: `-empty` renders a day with nothing watched; every Home layer says its empty word.
import Foundation
import Testing
@testable import Brian

@MainActor
struct EmptyStateTests {
    private func emptyState() async -> AppState {
        let state = AppState(demo: true)
        state.startEmpty()
        await state.refreshToday()
        await state.refreshProtocol()
        return state
    }

    @Test func emptyDemoLoadsWithoutAnError() async {
        let state = await emptyState()
        #expect(state.lastError == nil)
        #expect(!state.watching)
        #expect(state.watchingSince == nil)
        #expect(state.episodes.isEmpty)
        #expect(state.decisions.isEmpty)
        #expect(state.sessions.isEmpty)
        #expect(state.protocolItems.isEmpty)
        #expect(state.healthspan?.hoursToday == 0)
        #expect(state.healthspan?.measuredCount == 0)
    }

    @Test func everyHomeLayerShowsItsEmptyWord() async {
        let state = await emptyState()
        let now = Date()
        let metrics = state.homeMetrics(now: now)
        #expect(metrics.daylight == nil)
        #expect(metrics.screens == nil)
        #expect(metrics.watchedLine == "Not watched yet today")
        #expect(state.summaryCard.body == .empty)
        #expect(state.protocolSummary(now: now).isEmpty)
        #expect(state.sessionsSummary(now: now).isEmpty)
        #expect(state.todayStats(now: now).allSatisfy { $0.isEmpty })
        #expect(Ledger.entries(decisions: state.decisions, episodes: state.episodes).isEmpty)
    }

    @Test func nothingHasBeenSpoken() async {
        let state = await emptyState()
        #expect(state.streamStats.lastSpokenText == nil)
        #expect(state.streamStats.lastSpokenAt == nil)
    }

    @Test func summaryIsNotFetchedWithNothingWatched() async {
        let state = await emptyState()
        await state.loadSummaryIfNeeded()
        #expect(state.summary == nil)
        #expect(!state.summaryFailed)
    }

    @Test func aProtocolCanBeSetUpFromEmpty() async {
        let state = await emptyState()
        await state.addProtocolItem(name: "Morning light", kind: "walk", windowStart: "07:00",
                                    windowEnd: "10:00", days: [0, 1, 2, 3, 4, 5, 6])
        #expect(state.protocolItems.map(\.name) == ["Morning light"])
        #expect(state.lastError == nil)
    }
}
