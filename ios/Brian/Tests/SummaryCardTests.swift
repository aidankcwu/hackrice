// DEMO_UI_PRD.md "Home · 2. Daily summary": what the card says in each state, and the
// recap's wire shape.
import Foundation
import Testing
@testable import Brian

@MainActor
struct SummaryCardTests {
    private let chicago = TimeZone(identifier: "America/Chicago")!
    private let us = Locale(identifier: "en_US")
    /// 2026-09-24 18:12 in Chicago.
    private var sixTwelve: Date {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = chicago
        return c.date(from: DateComponents(year: 2026, month: 9, day: 24, hour: 18, minute: 12))!
    }
    private let recap = Recap(headline: "An even afternoon.", paragraphs: ["One.", "Two."],
                              suggestions: ["Walk.", "Call.", "Water."])

    private func card(summary: DailySummary? = nil, loading: Bool = false, failed: Bool = false,
                      hasEpisodes: Bool = true) -> SummaryCard {
        SummaryCard.derive(summary: summary, loading: loading, failed: failed, hasEpisodes: hasEpisodes,
                           locale: us, timeZone: chicago)
    }

    @Test func noEpisodesIsTheEmptySentenceWithoutRefresh() {
        let c = card(hasEpisodes: false)
        #expect(c.body == .empty)
        #expect(c.status == nil)
        #expect(!c.showsRefresh)
        #expect(SummaryCard.emptySentence
                == "Nothing watched yet today. Put the glasses on and the summary writes itself.")
    }

    @Test func aFailedFetchDoesNotHideTheEmptySentence() {
        #expect(card(failed: true, hasEpisodes: false).body == .empty)
    }

    @Test func cachedRecapShowsItsTimeAndRefresh() {
        let c = card(summary: DailySummary(recap: recap, fetchedAt: sixTwelve))
        #expect(c.body == .recap(recap))
        #expect(c.status == "as of 6:12\u{202F}PM" || c.status == "as of 6:12 PM")
        #expect(c.showsRefresh)
    }

    @Test func loadingKeepsTheOldTextAndSaysUpdating() {
        let c = card(summary: DailySummary(recap: recap, fetchedAt: sixTwelve), loading: true)
        #expect(c.body == .recap(recap))
        #expect(c.status == "Updating…")
        #expect(c.showsRefresh)
    }

    @Test func aFailedRefreshKeepsTheCachedRecap() {
        let c = card(summary: DailySummary(recap: recap, fetchedAt: sixTwelve), failed: true)
        #expect(c.body == .recap(recap))
        #expect(c.status?.hasPrefix("as of") == true)
    }

    @Test func firstLoadSaysUpdatingWithNoRefresh() {
        let c = card(loading: true)
        #expect(c.body == .writing)
        #expect(c.status == "Updating…")
        #expect(!c.showsRefresh)
    }

    @Test func aFailedFirstLoadOffersRefresh() {
        let c = card(failed: true)
        #expect(c.body == .failed)
        #expect(c.showsRefresh)
    }

    @Test func episodesBeforeAnyFetchReadAsWriting() {
        #expect(card().body == .writing)
    }

    // MARK: Recap decoding

    @Test func fixtureDecodesFromTheNestedNarrative() throws {
        let api = APIClient(mode: .fixtures, fixtureBundle: Bundle(for: AppState.self))
        let r: Recap = try api.fixture("recap_today")
        #expect(r.id == "r_7c41d0a2")
        #expect(r.headline.hasPrefix("An even afternoon"))
        #expect(r.paragraphs.count == 2)
        #expect(r.suggestions.count == 3)
        #expect(!r.headline.contains("!") && !r.paragraphs.joined().contains("!"))
    }

    @Test func flatRecapDecodesAndMissingListsAreEmpty() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let json = #"{"headline": "Quiet morning.", "generated_at": 1790111400.5}"#
        let r = try decoder.decode(Recap.self, from: Data(json.utf8))
        #expect(r.headline == "Quiet morning.")
        #expect(r.generatedAt == 1790111400.5)
        #expect(r.paragraphs.isEmpty && r.suggestions.isEmpty)
    }

    @Test func demoStateFetchesTheFixtureAndStopRefreshesIt() async {
        let state = AppState(demo: true, defaults: UserDefaults(suiteName: "SummaryCardTests")!,
                             tokenStore: InMemoryTokenStore())
        #expect(state.summaryCard.body == .empty)
        await state.refreshToday()
        await state.loadSummaryIfNeeded()
        guard case .recap(let r) = state.summaryCard.body else {
            Issue.record("expected the fixture recap")
            return
        }
        #expect(r.suggestions.count == 3)
        let first = state.summary?.fetchedAt
        await state.stopWatching()
        #expect(state.summary?.fetchedAt != first)
    }
}
