// DEMO_UI_PRD.md "Home · 4. Sessions": today's Start → Stop spans, newest first, each with
// its span, its length and its recap when the backend has written one. Pure; HomeView and
// SessionDetailView draw it.
import Foundation

struct SessionsSummary: Equatable {
    struct Row: Identifiable, Equatable {
        let session: WatchSession
        /// "3:25–3:39 PM"; "Since 3:53 PM" while the session is open.
        let span: String
        /// "14 min", "1 h 15 min"; runs to now while open.
        let duration: String
        /// The newest recap written for this session, nil until one exists.
        let recapID: String?
        /// Its body, once fetched.
        let recap: Recap?

        var id: String { session.id }
        var isOpen: Bool { session.endedT == nil }
        /// The row's second line: the headline, or nothing yet.
        var headline: String? { recap?.headline }

        /// What the detail screen shows in place of the recap.
        var recapState: RecapState {
            if let recap { return .recap(recap) }
            return isOpen ? .running : .writing
        }
    }

    enum RecapState: Equatable {
        case recap(Recap)
        /// Ended, and no recap body here yet: "Summary still writing…" and Refresh.
        case writing
        /// Still running: the backend writes the recap once it ends.
        case running
    }

    /// Newest first.
    let rows: [Row]
    /// Seconds watched across `rows`, clipped to the day.
    let totalSeconds: TimeInterval

    var isEmpty: Bool { rows.isEmpty }
    /// "Sessions · 3 · 43 min"; "Sessions" with none.
    var title: String {
        rows.isEmpty ? "Sessions" : "Sessions · \(rows.count) · \(Self.durationText(seconds: totalSeconds))"
    }

    static let emptySentence = "No sessions yet today."
    static let writingSentence = "Summary still writing…"
    static let runningSentence = "The summary writes itself when you stop watching."
    static let nothingLoggedSentence = "Nothing was logged in this session."

    /// Sessions that overlap `[dayStart, dayStart + 1 day)`, clipped to it and to `now`.
    /// `recaps` is `GET /api/recaps` (newest first); `bodies` the fetched bodies by recap id.
    static func derive(sessions: [WatchSession], recaps: [RecapListing], bodies: [String: Recap],
                       dayStart: Date, now: Date, calendar: Calendar = .current,
                       locale: Locale = .current, timeZone: TimeZone = .current) -> SessionsSummary {
        let dayEnd = calendar.date(byAdding: .day, value: 1, to: dayStart) ?? dayStart.addingTimeInterval(86_400)
        var rows: [(row: Row, seconds: TimeInterval)] = []
        for session in sessions {
            let seconds = watched(session, from: dayStart, to: min(dayEnd, now))
            guard seconds > 0 else { continue }
            // The listing is newest first, so the first match is the latest rewrite.
            let recapID = recaps.first { $0.sessionId == session.id }?.id
            let row = Row(session: session,
                          span: span(session, locale: locale, timeZone: timeZone),
                          duration: durationText(seconds: length(session, now: now)),
                          recapID: recapID,
                          recap: recapID.flatMap { bodies[$0] })
            rows.append((row, seconds))
        }
        rows.sort { $0.row.session.startedT > $1.row.session.startedT }
        return SessionsSummary(rows: rows.map(\.row), totalSeconds: rows.reduce(0) { $0 + $1.seconds })
    }

    /// Seconds of `session` inside `[start, end]`; an open session runs to `end`.
    static func watched(_ session: WatchSession, from start: Date, to end: Date) -> TimeInterval {
        let stop = min(session.endedT ?? end.timeIntervalSince1970, end.timeIntervalSince1970)
        return max(0, stop - max(session.startedT, start.timeIntervalSince1970))
    }

    /// The whole session, open ones up to `now`.
    static func length(_ session: WatchSession, now: Date) -> TimeInterval {
        max(0, (session.endedT ?? now.timeIntervalSince1970) - session.startedT)
    }

    /// "3:25–3:39 PM", "11:40 AM–12:25 PM", or "Since 3:53 PM" while open.
    static func span(_ session: WatchSession, locale: Locale = .current, timeZone: TimeZone = .current) -> String {
        let start = Date(timeIntervalSince1970: session.startedT)
        guard let ended = session.endedT else {
            return "Since " + SummaryCard.time(start, locale: locale, timeZone: timeZone)
        }
        let formatter = DateIntervalFormatter()
        formatter.locale = locale
        formatter.timeZone = timeZone
        formatter.dateStyle = .none
        formatter.timeStyle = .short
        return formatter.string(from: start, to: Date(timeIntervalSince1970: max(ended, session.startedT)))
    }

    /// Whole minutes ("14 min", "1 h 15 min"); "Under a minute" below one.
    static func durationText(seconds: TimeInterval) -> String {
        let minutes = Int(seconds / 60)
        return minutes == 0 ? "Under a minute" : TodayStats.durationText(minutes: minutes)
    }

    /// The log rows that fall inside the session, newest first; an open one runs to `now`.
    static func entries(_ entries: [LedgerEntry], in session: WatchSession, now: Date) -> [LedgerEntry] {
        let start = session.startedT
        let end = session.endedT ?? now.timeIntervalSince1970
        return entries.filter {
            let t = $0.date.timeIntervalSince1970
            return t >= start && t <= end
        }
    }
}
