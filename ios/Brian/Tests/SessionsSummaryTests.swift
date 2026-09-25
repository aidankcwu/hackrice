// DEMO_UI_PRD.md "Home · 4. Sessions": which sessions are today's, their order, span and
// duration words, the recap matched to each, and the log rows inside one.
import Foundation
import Testing
@testable import Brian

@MainActor
struct SessionsSummaryTests {
    private let chicago = TimeZone(identifier: "America/Chicago")!
    private let us = Locale(identifier: "en_US")
    private var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = chicago
        return c
    }

    private func at(_ hour: Int, _ minute: Int = 0, day: Int = 24) -> Double {
        calendar.date(from: DateComponents(year: 2026, month: 9, day: day, hour: hour, minute: minute))!
            .timeIntervalSince1970
    }

    private var now: Date { Date(timeIntervalSince1970: at(18)) }
    private var dayStart: Date { calendar.startOfDay(for: now) }

    private func summary(_ sessions: [WatchSession], recaps: [RecapListing] = [],
                         bodies: [String: Recap] = [:], now: Date? = nil) -> SessionsSummary {
        SessionsSummary.derive(sessions: sessions, recaps: recaps, bodies: bodies, dayStart: dayStart,
                               now: now ?? self.now, calendar: calendar, locale: us, timeZone: chicago)
    }

    /// ICU puts a narrow no-break space before AM / PM and thin spaces round a range dash.
    private func plain(_ text: String) -> String {
        String(text.map { ["\u{202F}", "\u{2009}", "\u{00A0}"].contains($0) ? " " : $0 })
            .replacingOccurrences(of: " – ", with: "–")
    }

    // MARK: Grouping

    @Test func todaysSessionsNewestFirstWithTheTotal() {
        let sessions = [
            WatchSession(id: "a", startedT: at(8, 40), endedT: at(10)),        // 1 h 20 min
            WatchSession(id: "c", startedT: at(15, 10), endedT: at(16, 10)),   // 1 h
            WatchSession(id: "b", startedT: at(12, 5), endedT: at(12, 55)),    // 50 min
        ]
        let s = summary(sessions)
        #expect(s.rows.map(\.id) == ["c", "b", "a"])
        #expect(s.title == "Sessions · 3 · 3 h 10 min")
        #expect(s.rows.map(\.duration) == ["1 h", "50 min", "1 h 20 min"])
    }

    @Test func yesterdaysSessionsAreLeftOutAndOvernightOnesClipped() {
        let sessions = [
            WatchSession(id: "old", startedT: at(20, day: 23), endedT: at(21, day: 23)),
            WatchSession(id: "overnight", startedT: at(23, 30, day: 23), endedT: at(0, 30)),
        ]
        let s = summary(sessions)
        #expect(s.rows.map(\.id) == ["overnight"])
        // The total counts today's half hour; the row keeps the whole session's length.
        #expect(s.title == "Sessions · 1 · 30 min")
        #expect(s.rows[0].duration == "1 h")
    }

    @Test func anOpenSessionRunsToNow() {
        let s = summary([WatchSession(id: "open", startedT: at(17, 46))])
        #expect(s.title == "Sessions · 1 · 14 min")
        #expect(plain(s.rows[0].span) == "Since 5:46 PM")
        #expect(s.rows[0].isOpen)
        #expect(s.rows[0].recapState == .running)
    }

    @Test func noSessionsSaysSo() {
        let s = summary([])
        #expect(s.isEmpty)
        #expect(s.title == "Sessions")
        #expect(SessionsSummary.emptySentence == "No sessions yet today.")
    }

    @Test func aFixtureDayCountsWhenItIsTheDay() {
        // Demo mode passes the fixture day's midnight: its sessions count, whatever today is.
        let fixture = WatchSession(id: "f", startedT: at(15, 25, day: 22), endedT: at(15, 39, day: 22))
        let fixtureDay = calendar.startOfDay(for: Date(timeIntervalSince1970: fixture.startedT))
        let s = SessionsSummary.derive(sessions: [fixture], recaps: [], bodies: [:], dayStart: fixtureDay,
                                       now: now, calendar: calendar, locale: us, timeZone: chicago)
        #expect(s.title == "Sessions · 1 · 14 min")
        #expect(summary([fixture]).isEmpty)
    }

    // MARK: Words

    @Test func spansShareTheDayPeriodWhenTheyCan() {
        let afternoon = WatchSession(id: "a", startedT: at(14, 10), endedT: at(15, 25))
        let noon = WatchSession(id: "b", startedT: at(11, 40), endedT: at(12, 25))
        #expect(plain(SessionsSummary.span(afternoon, locale: us, timeZone: chicago)) == "2:10–3:25 PM")
        #expect(plain(SessionsSummary.span(noon, locale: us, timeZone: chicago)) == "11:40 AM–12:25 PM")
    }

    @Test func durationWords() {
        #expect(SessionsSummary.durationText(seconds: 20) == "Under a minute")
        #expect(SessionsSummary.durationText(seconds: 14 * 60 + 59) == "14 min")
        #expect(SessionsSummary.durationText(seconds: 75 * 60) == "1 h 15 min")
        #expect(SessionsSummary.durationText(seconds: 125 * 60) == "2 h 05 min")
    }

    // MARK: Recaps

    @Test func theNewestRecapForTheSessionIsItsHeadline() {
        let session = WatchSession(id: "s1", startedT: at(14), endedT: at(15))
        let recaps = [
            RecapListing(id: "r_new", sessionId: "s1", generatedAt: at(15, 5)),
            RecapListing(id: "r_other", sessionId: "s2", generatedAt: at(15, 2)),
            RecapListing(id: "r_old", sessionId: "s1", generatedAt: at(15, 1)),
        ]
        let bodies = ["r_new": Recap(headline: "New"), "r_old": Recap(headline: "Old")]
        let row = summary([session], recaps: recaps, bodies: bodies).rows[0]
        #expect(row.recapID == "r_new")
        #expect(row.headline == "New")
        #expect(row.recapState == .recap(Recap(headline: "New")))
    }

    @Test func anEndedSessionWithoutARecapIsStillWriting() {
        let session = WatchSession(id: "s1", startedT: at(14), endedT: at(15))
        #expect(summary([session]).rows[0].recapState == .writing)
        #expect(summary([session]).rows[0].headline == nil)
        // Listed but the body not fetched yet: still writing, so Refresh fetches it.
        let listed = [RecapListing(id: "r1", sessionId: "s1", generatedAt: at(15, 1))]
        #expect(summary([session], recaps: listed).rows[0].recapState == .writing)
    }

    // MARK: Log rows inside a session

    @Test func logRowsInsideTheSessionOnly() {
        func entry(_ id: String, _ t: Double) -> LedgerEntry {
            LedgerEntry(id: id, date: Date(timeIntervalSince1970: t), label: id, kind: "meal",
                        outcome: nil, decision: nil, reported: nil)
        }
        let entries = [entry("after", at(15, 1)), entry("end", at(15)), entry("mid", at(14, 30)),
                       entry("start", at(14)), entry("before", at(13, 59))]
        let closed = WatchSession(id: "s", startedT: at(14), endedT: at(15))
        #expect(SessionsSummary.entries(entries, in: closed, now: now).map(\.id) == ["end", "mid", "start"])
        let open = WatchSession(id: "o", startedT: at(14, 30))
        #expect(SessionsSummary.entries(entries, in: open, now: Date(timeIntervalSince1970: at(15)))
            .map(\.id) == ["end", "mid"])
    }

    // MARK: Watched line agrees with the Sessions total

    @Test func watchedLineUsesTheSameDay() {
        let fixture = WatchSession(id: "f", startedT: at(15, 25, day: 22), endedT: at(15, 39, day: 22))
        let fixtureDay = calendar.startOfDay(for: Date(timeIntervalSince1970: fixture.startedT))
        let metrics = HomeMetrics.derive(episodes: [], sessions: [fixture], watchingSince: nil, now: now,
                                         calendar: calendar, dayStart: fixtureDay)
        #expect(metrics.watchedLine == "Watched 14 min today")
    }

    // MARK: Demo fixtures

    @Test func demoLoadsThreeSessionsWithTheirRecapsAndLogRows() async throws {
        let state = AppState(demo: true)
        await state.refreshToday()
        #expect(state.sessions.count == 3)
        #expect(state.recapListings.count == 3)
        #expect(state.recapBodies.count == 3)
        let s = state.sessionsSummary(now: Date())
        #expect(s.title == "Sessions · 3 · 43 min")
        #expect(state.homeMetrics(now: Date()).watchedLine == "Watched 43 min today")
        #expect(s.rows.allSatisfy { $0.headline != nil })
        let newest = try #require(s.rows.first)
        let entries = SessionsSummary.entries(Ledger.entries(decisions: state.decisions, episodes: state.episodes),
                                              in: newest.session, now: Date())
        #expect(!entries.isEmpty)
        #expect(entries.allSatisfy { $0.date.timeIntervalSince1970 >= newest.session.startedT })
    }
}
