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

    // MARK: Stats sheet (D-007)

    private let chicago = TimeZone(identifier: "America/Chicago")!
    private let us = Locale(identifier: "en_US")

    /// An episode at `hour:minute` today in Chicago lasting `minutes`.
    private func at(_ kind: String, _ hour: Int, _ minute: Int, minutes: Double, label: String? = nil) -> Episode {
        let start = calendar.date(from: DateComponents(year: 2026, month: 9, day: 24, hour: hour, minute: minute))!
            .timeIntervalSince1970
        return Episode(id: UUID().uuidString, kind: kind, label: label, startT: start, endT: start + minutes * 60,
                       reported: nil)
    }

    private func stats(_ episodes: [Episode]) -> [String: TodayStats.Cell] {
        let cells = TodayStats.derive(episodes: episodes, now: now, locale: us, timeZone: chicago)
        return Dictionary(uniqueKeysWithValues: cells.map { ($0.label, $0) })
    }

    /// ICU puts U+202F before AM/PM and U+2009 around the en dash; compare with plain spaces.
    private func plain(_ s: String?) -> String? {
        s?.replacingOccurrences(of: "\u{202F}", with: " ").replacingOccurrences(of: "\u{2009}", with: "")
    }

    @Test func eightCellsInThePRDsOrder() {
        let labels = TodayStats.derive(episodes: [], now: now).map(\.label)
        #expect(labels == ["First daylight", "Eating window", "Last caffeine", "Screens",
                           "Longest focus", "Alcohol", "Time with people", "Hydration"])
    }

    @Test func everyEmptyWord() {
        let cells = stats([])
        #expect(cells.values.filter { !$0.isEmpty }.isEmpty)
        #expect(cells["First daylight"]?.text == "Not yet")
        #expect(cells["Eating window"]?.text == "No meals seen")
        #expect(cells["Last caffeine"]?.text == "None seen")
        #expect(cells["Screens"]?.text == "None seen")
        #expect(cells["Longest focus"]?.text == "—")
        #expect(cells["Alcohol"]?.text == "None seen")
        #expect(cells["Time with people"]?.text == "Not tracked yet")
        #expect(cells["Hydration"]?.text == "Not tracked yet")
    }

    @Test func firstDaylightIsTheEarliestOutdoorStart() {
        let cells = stats([at("outdoor_block", 13, 5, minutes: 20), at("outdoor_block", 8, 52, minutes: 10),
                           at("screen_block", 7, 0, minutes: 30)])
        #expect(plain(cells["First daylight"]?.value) == "8:52 AM")
    }

    @Test func eatingWindowRunsFirstMealStartToLastMealEnd() {
        let cells = stats([at("meal", 12, 10, minutes: 20), at("food_sighting", 19, 30, minutes: 15),
                           at("meal", 15, 0, minutes: 5), at("caffeine_sighting", 20, 0, minutes: 5)])
        let eating = cells["Eating window"]
        #expect(plain(eating?.value) == "12:10–7:45 PM")
        #expect(eating?.detail == "7 h 35 min")
        #expect(plain(eating?.text) == "12:10–7:45 PM · 7 h 35 min")
        let crossing = stats([at("meal", 11, 50, minutes: 10), at("meal", 13, 0, minutes: 10)])["Eating window"]
        #expect(plain(crossing?.value) == "11:50 AM–1:10 PM")
        #expect(crossing?.detail == "1 h 20 min")
    }

    @Test func lastCaffeineIsTheLatestStart() {
        let cells = stats([at("caffeine_sighting", 8, 0, minutes: 1), at("caffeine_sighting", 14, 40, minutes: 1),
                           at("caffeine_sighting", 11, 0, minutes: 1)])
        #expect(plain(cells["Last caffeine"]?.value) == "2:40 PM")
    }

    @Test func screensSumAndLongestFocus() {
        let cells = stats([at("screen_block", 9, 0, minutes: 48), at("screen_block", 13, 0, minutes: 47),
                           at("screen_block", 16, 0, minutes: 30), at("outdoor_block", 10, 0, minutes: 90)])
        #expect(cells["Screens"]?.value == "2 h 05 min")
        #expect(cells["Longest focus"]?.value == "48 min")
        #expect(stats([at("screen_block", 9, 0, minutes: 0.3)])["Longest focus"]?.value == "Under a minute")
    }

    @Test func alcoholCountsSightings() {
        #expect(stats([at("alcohol_sighting", 19, 0, minutes: 1)])["Alcohol"]?.value == "1 sighting")
        #expect(stats([at("alcohol_sighting", 19, 0, minutes: 1),
                       at("alcohol_sighting", 20, 0, minutes: 1)])["Alcohol"]?.value == "2 sightings")
    }

    @Test func timeWithPeopleSumsSocialKindsAndLabels() {
        let cells = stats([at("conversation", 9, 0, minutes: 25), at("social", 12, 0, minutes: 10),
                           at("meal", 13, 0, minutes: 30, label: "lunch with people"),
                           at("meal", 18, 0, minutes: 30, label: "dinner alone")])
        #expect(cells["Time with people"]?.value == "1 h 05 min")
    }

    @Test func hydrationCountsWaterLabelsButNotAlcoholOrCoffee() {
        let cells = stats([at("food_sighting", 9, 0, minutes: 1, label: "water bottle"),
                           at("food_sighting", 12, 0, minutes: 1, label: "drink, glass of water"),
                           at("alcohol_sighting", 19, 0, minutes: 1, label: "drink in hand"),
                           at("caffeine_sighting", 8, 0, minutes: 1, label: "coffee drink")])
        #expect(cells["Hydration"]?.value == "2 sightings")
    }

    @Test func statsFromTheFixtureDay() throws {
        let api = APIClient(mode: .fixtures, fixtureBundle: Bundle(for: AppState.self))
        let episodes: [Episode] = try api.fixture("today_episodes")
        let cells = Dictionary(uniqueKeysWithValues: TodayStats.derive(episodes: episodes, now: Date(), locale: us,
                                                                      timeZone: chicago).map { ($0.label, $0) })
        #expect(plain(cells["First daylight"]?.value) == "3:29 PM")
        #expect(plain(cells["Eating window"]?.value) == "3:28–4:08 PM")
        #expect(cells["Eating window"]?.detail == "40 min")
        #expect(plain(cells["Last caffeine"]?.value) == "4:06 PM")
        #expect(cells["Screens"]?.value == "28 min")
        #expect(cells["Longest focus"]?.value != nil)
        #expect(cells["Alcohol"]?.value == "7 sightings")
        #expect(cells["Time with people"]?.isEmpty == true)
        #expect(cells["Hydration"]?.isEmpty == true)
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
