// DEMO_UI_PRD.md "Home · 1. Metrics": tiles, watched line, and the hero sheet's words.
import Foundation
import Testing
@testable import Brian

@MainActor
struct TodayStatsTests {
    private var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "America/Chicago")!
        return c
    }
    /// 2026-09-24 18:00 in Chicago.
    private var now: Date {
        calendar.date(from: DateComponents(year: 2026, month: 9, day: 24, hour: 18))!
    }
    private var midnight: Double { calendar.startOfDay(for: now).timeIntervalSince1970 }

    private func episode(_ kind: String, _ start: Double, _ end: Double?) -> Episode {
        Episode(id: UUID().uuidString, kind: kind, label: nil, startT: start, endT: end, reported: nil)
    }

    // MARK: TodayStats.minutes(kind:)

    @Test func minutesSumsOneKindOnly() {
        let t = now.timeIntervalSince1970 - 3600
        let episodes = [
            episode("outdoor_block", t, t + 600),
            episode("outdoor_block", t + 1000, t + 1300),
            episode("screen_block", t, t + 3000),
        ]
        #expect(TodayStats.minutes(kind: "outdoor_block", episodes: episodes, now: now) == 15)
        #expect(TodayStats.minutes(kind: "screen_block", episodes: episodes, now: now) == 50)
    }

    @Test func minutesCountsAnOpenEpisodeUpToNow() {
        let episodes = [episode("outdoor_block", now.timeIntervalSince1970 - 540, nil)]
        #expect(TodayStats.minutes(kind: "outdoor_block", episodes: episodes, now: now) == 9)
    }

    @Test func anOpenEpisodeTrustsTheServersRunningLength() {
        // Opened two days ago by the clock, but the server says it has run 90 s.
        let stale = Episode(id: "e", kind: "outdoor_block", label: nil, startT: now.timeIntervalSince1970 - 172_800,
                            endT: nil, reported: nil, durationS: 90)
        #expect(TodayStats.minutes(kind: "outdoor_block", episodes: [stale], now: now) == 2)
    }

    @Test func minutesIsNilWhenTheKindWasNeverSeen() {
        let episodes = [episode("meal", now.timeIntervalSince1970 - 600, now.timeIntervalSince1970)]
        #expect(TodayStats.minutes(kind: "outdoor_block", episodes: episodes, now: now) == nil)
        #expect(TodayStats.minutes(kind: "screen_block", episodes: [], now: now) == nil)
    }

    @Test func minutesFromTheFixtureDay() throws {
        let api = APIClient(mode: .fixtures, fixtureBundle: Bundle(for: AppState.self))
        let episodes: [Episode] = try api.fixture("today_episodes")
        // Days after the fixture was captured: the open episode must not grow with the clock.
        let fixtureNow = Date()
        #expect(TodayStats.minutes(kind: "outdoor_block", episodes: episodes, now: fixtureNow) == 10)
        #expect(TodayStats.minutes(kind: "screen_block", episodes: episodes, now: fixtureNow) == 28)
    }

    @Test func durationText() {
        #expect(TodayStats.durationText(minutes: 0) == "0 min")
        #expect(TodayStats.durationText(minutes: 14) == "14 min")
        #expect(TodayStats.durationText(minutes: 60) == "1 h")
        #expect(TodayStats.durationText(minutes: 125) == "2 h 05 min")
        #expect(TodayStats.durationText(minutes: 190) == "3 h 10 min")
    }

    // MARK: Watched line

    @Test func watchedUsesSessionsWhenPresent() {
        let sessions = [
            WatchSession(id: "3", startedT: midnight + 15 * 3600, endedT: midnight + 16 * 3600 + 15 * 60),
            WatchSession(id: "2", startedT: midnight + 10 * 3600, endedT: midnight + 11 * 3600),
            WatchSession(id: "1", startedT: midnight + 8 * 3600, endedT: midnight + 8 * 3600 + 55 * 60),
        ]
        // The watching span is ignored once the backend reports sessions.
        let metrics = HomeMetrics.derive(episodes: [], sessions: sessions,
                                         watchingSince: now.addingTimeInterval(-60),
                                         now: now, calendar: calendar)
        #expect(metrics.watchedLine == "Watched 3 h 10 min today")
    }

    @Test func watchedCountsAnOpenSessionToNowAndClipsYesterday() {
        let sessions = [
            WatchSession(id: "2", startedT: now.timeIntervalSince1970 - 20 * 60, endedT: nil),
            WatchSession(id: "1", startedT: midnight - 3600, endedT: midnight + 30 * 60),
            WatchSession(id: "0", startedT: midnight - 7200, endedT: midnight - 3600),
        ]
        #expect(HomeMetrics.watchedSeconds(sessions: sessions, watchingSince: nil, now: now,
                                           calendar: calendar) == 50 * 60)
    }

    @Test func watchedFallsBackToTheWatchingSpan() {
        let metrics = HomeMetrics.derive(episodes: [], sessions: [],
                                         watchingSince: now.addingTimeInterval(-14 * 60),
                                         now: now, calendar: calendar)
        #expect(metrics.watchedLine == "Watched 14 min today")
        // Sessions that all ended yesterday do not count as today's.
        let old = [WatchSession(id: "0", startedT: midnight - 7200, endedT: midnight - 3600)]
        #expect(HomeMetrics.derive(episodes: [], sessions: old,
                                   watchingSince: now.addingTimeInterval(-14 * 60),
                                   now: now, calendar: calendar).watchedLine == "Watched 14 min today")
    }

    @Test func notWatchedYet() {
        let metrics = HomeMetrics.derive(episodes: [], sessions: [], watchingSince: nil,
                                         now: now, calendar: calendar)
        #expect(metrics.watchedLine == "Not watched yet today")
        #expect(metrics.daylight == nil)
        #expect(metrics.screens == nil)
        #expect(HomeMetrics.watchedLine(seconds: 20) == "Watched under a minute today")
    }

    @Test func tilesReadTheirKinds() {
        let t = now.timeIntervalSince1970 - 7200
        let metrics = HomeMetrics.derive(
            episodes: [episode("outdoor_block", t, t + 12 * 60), episode("screen_block", t, t + 125 * 60)],
            sessions: [], watchingSince: nil, now: now, calendar: calendar)
        #expect(metrics.daylight == "12 min")
        #expect(metrics.screens == "2 h 05 min")
    }

    // MARK: Hero sheet words

    private func factor(_ key: String, dose: Double?, hours: Double = 0, provenance: String = "seeded",
                        basis: String = "phone", measured: Bool = true, source: String? = nil) -> HealthFactor {
        HealthFactor(key: key, label: key, dose: dose, hours: hours, provenance: provenance,
                     basis: basis, measured: measured, source: source)
    }

    @Test func provenanceWords() {
        #expect(HealthFactorText.provenanceWord(factor("x", dose: 1, provenance: "live", basis: "glasses")) == "Glasses")
        #expect(HealthFactorText.provenanceWord(factor("x", dose: 1, provenance: "derived", basis: "glasses")) == "Glasses")
        #expect(HealthFactorText.provenanceWord(factor("x", dose: 1, provenance: "derived", basis: "apple_watch")) == "Seeded")
        #expect(HealthFactorText.provenanceWord(factor("x", dose: 1, provenance: "seeded", basis: "whoop")) == "Seeded")
        #expect(HealthFactorText.provenanceWord(factor("x", dose: nil, provenance: "missing", basis: "openaq",
                                                       measured: false)) == "Missing")
    }

    @Test func doseWithUnit() {
        #expect(HealthFactorText.dose(factor("steps", dose: 5500)) == "5,500 steps")
        #expect(HealthFactorText.dose(factor("vilpa_min", dose: 1.4)) == "1.4 min")
        #expect(HealthFactorText.dose(factor("nature_min_wk", dose: 94.075)) == "94 min this week")
        #expect(HealthFactorText.dose(factor("fitness_pct", dose: 77.5)) == "78th percentile")
        #expect(HealthFactorText.dose(factor("alcohol_drinks", dose: 1)) == "1 drink")
        #expect(HealthFactorText.dose(factor("alcohol_drinks", dose: 2)) == "2 drinks")
        #expect(HealthFactorText.dose(factor("smoker", dose: 0)) == "No")
        #expect(HealthFactorText.dose(factor("recovery_ratio", dose: 1.02)) == "1× baseline")
        #expect(HealthFactorText.dose(factor("pm25", dose: nil, provenance: "missing", measured: false)) == "Not measured")
        #expect(HealthFactorText.dose(factor("unknown_key", dose: 3)) == "3")
    }

    @Test func citationStopsAtTheFinding() {
        let f = factor("steps", dose: 1, source: "Paluch 2022 Lancet Public Health (15 cohorts, n=47,471): vs ~3.5k, Q2 5.8k HR 0.60")
        #expect(HealthFactorText.citation(f) == "Paluch 2022 Lancet Public Health (15 cohorts, n=47,471)")
        #expect(HealthFactorText.citation(factor("x", dose: 1, source: "WHO 2018")) == "WHO 2018")
        #expect(HealthFactorText.citation(factor("x", dose: 1, source: nil)) == nil)
    }

    @Test func biggestEffectFirstMissingLast() {
        let ordered = HealthFactorText.ordered([
            factor("pm25", dose: nil, hours: 0, provenance: "missing", measured: false),
            factor("a", dose: 1, hours: 0.11),
            factor("b", dose: 1, hours: -1.14),
            factor("c", dose: 1, hours: 0.97),
            factor("d", dose: 1, hours: 0),
        ])
        #expect(ordered.map(\.key) == ["b", "c", "a", "d", "pm25"])
    }

    @Test func fixtureFactorsDecode() throws {
        let api = APIClient(mode: .fixtures, fixtureBundle: Bundle(for: AppState.self))
        let h: Healthspan = try api.fixture("today_healthspan")
        let factors = try #require(h.factors)
        #expect(factors.count == 20)
        let steps = try #require(factors.first { $0.key == "steps" })
        #expect(steps.label == "Daily steps")
        #expect(HealthFactorText.dose(steps) == "5,500 steps")
        #expect(HealthFactorText.provenanceWord(steps) == "Seeded")
        let social = try #require(factors.first { $0.key == "social_index" })
        #expect(HealthFactorText.provenanceWord(social) == "Glasses")
        #expect(HealthFactorText.ordered(factors).first?.key == "social_index")
    }
}
