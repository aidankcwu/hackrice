// DEMO_UI_PRD.md "Home · 2. Daily summary": what the card says, from the cached recap,
// whether a fetch is running, and whether today has any episode. Pure; HomeView draws it.
import Foundation

/// The last recap Home fetched, with the phone time it arrived ("as of").
struct DailySummary: Equatable {
    let recap: Recap
    let fetchedAt: Date
}

struct SummaryCard: Equatable {
    enum Body: Equatable {
        /// Nothing watched yet today, nothing cached.
        case empty
        /// First fetch running, nothing cached yet.
        case writing
        /// The fetch failed with nothing cached: a sentence and Refresh.
        case failed
        case recap(Recap)
    }

    /// "as of 6:12 PM", "Updating…", or nil (nothing to date).
    let status: String?
    let body: Body
    let showsRefresh: Bool

    static let emptySentence = "Nothing watched yet today. Put the glasses on and the summary writes itself."
    static let writingSentence = "Writing today's summary…"
    static let failedSentence = "The summary did not load. Tap Refresh to try again."
    static let updating = "Updating…"

    static func derive(summary: DailySummary?, loading: Bool, failed: Bool, hasEpisodes: Bool,
                       locale: Locale = .current, timeZone: TimeZone = .current) -> SummaryCard {
        if let summary {
            // The old text stays while a new one loads.
            return SummaryCard(status: loading ? updating : "as of \(time(summary.fetchedAt, locale: locale, timeZone: timeZone))",
                               body: .recap(summary.recap), showsRefresh: true)
        }
        if loading { return SummaryCard(status: updating, body: .writing, showsRefresh: false) }
        if !hasEpisodes { return SummaryCard(status: nil, body: .empty, showsRefresh: false) }
        if failed { return SummaryCard(status: nil, body: .failed, showsRefresh: true) }
        // Episodes arrived but no fetch has started yet: Home starts one on appearance.
        return SummaryCard(status: nil, body: .writing, showsRefresh: false)
    }

    /// "6:12 PM" in the phone's locale.
    static func time(_ date: Date, locale: Locale = .current, timeZone: TimeZone = .current) -> String {
        date.formatted(Date.FormatStyle(date: .omitted, time: .shortened, locale: locale, timeZone: timeZone))
    }
}
