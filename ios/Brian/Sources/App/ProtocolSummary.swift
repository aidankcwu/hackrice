// DEMO_UI_PRD.md "Home · 3. Protocol card" and "Protocol tab": what each of today's items
// says ("Seen 8:42 AM", "Done", "Missed", "Open until 10 PM", "Later"), in window order,
// and the "3 of 5" count. Pure; HomeView's card and ProtocolView both draw it.
import Foundation

struct ProtocolSummary: Equatable {
    enum Tone: Equatable {
        /// Seen or done: ink, with a filled / outlined checkmark.
        case complete
        /// Missed: `Brian.cost`, with `xmark.circle`.
        case missed
        /// Open or later: muted words, no symbol.
        case pending
    }

    struct Row: Equatable, Identifiable {
        let id: String
        let name: String
        let kind: String
        /// "7:00–10:00 AM"
        let window: String
        let state: String
        /// SF Symbol beside the state word; nil for open and later.
        let stateSymbol: String?
        let tone: Tone
        /// The checkbox on the Protocol tab: filled when seen or done.
        let checked: Bool
    }

    let rows: [Row]
    let completed: Int

    var total: Int { rows.count }
    var isEmpty: Bool { rows.isEmpty }
    /// "3 of 5"
    var count: String { "\(completed) of \(total)" }

    static func derive(items: [ProtocolItem], now: Date,
                       locale: Locale = .current, timeZone: TimeZone = .current) -> ProtocolSummary {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let clock = calendar.dateComponents([.hour, .minute], from: now)
        let nowMinutes = (clock.hour ?? 0) * 60 + (clock.minute ?? 0)

        let ordered = items.sorted {
            ($0.windowStart, $0.windowEnd, $0.name) < ($1.windowStart, $1.windowEnd, $1.name)
        }
        let rows = ordered.map { item -> Row in
            let start = minutes(item.windowStart)
            let end = minutes(item.windowEnd)
            let window = windowText(start: start, end: end, locale: locale) ?? "\(item.windowStart)–\(item.windowEnd)"
            func row(_ state: String, _ symbol: String?, _ tone: Tone, checked: Bool = false) -> Row {
                Row(id: item.id, name: item.name, kind: item.kind, window: window,
                    state: state, stateSymbol: symbol, tone: tone, checked: checked)
            }
            switch item.status.lowercased() {
            case "seen":
                let at = item.seenAt.map { " " + time(Date(timeIntervalSince1970: $0), locale: locale, timeZone: timeZone) } ?? ""
                return row("Seen" + at, "checkmark.circle.fill", .complete, checked: true)
            case "done":
                return row("Done", "checkmark.circle", .complete, checked: true)
            case "missed":
                return row("Missed", "xmark.circle", .missed)
            default:
                // waiting or undone: the clock decides.
                guard let start, let end else { return row("Later", nil, .pending) }
                if nowMinutes < start { return row("Later", nil, .pending) }
                if nowMinutes < end {
                    return row("Open until \(clockText(end, locale: locale))", nil, .pending)
                }
                return row("Missed", "xmark.circle", .missed)
            }
        }
        return ProtocolSummary(rows: rows, completed: rows.filter(\.checked).count)
    }

    /// "08:30" → 510; nil when the backend sent something else.
    static func minutes(_ hhmm: String) -> Int? {
        let parts = hhmm.split(separator: ":")
        guard parts.count >= 2, let h = Int(parts[0]), let m = Int(parts[1]),
              (0..<24).contains(h), (0..<60).contains(m) else { return nil }
        return h * 60 + m
    }

    /// "10 PM", "9:30 PM": a time of day without ":00".
    static func clockText(_ minutes: Int, locale: Locale = .current) -> String {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.setLocalizedDateFormatFromTemplate(minutes % 60 == 0 ? "j" : "jmm")
        return formatter.string(from: Date(timeIntervalSince1970: TimeInterval(minutes * 60)))
    }

    /// "7:00–10:00 AM" in the locale's own time style.
    static func windowText(start: Int?, end: Int?, locale: Locale = .current) -> String? {
        guard let start, let end else { return nil }
        let formatter = DateIntervalFormatter()
        formatter.locale = locale
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.dateStyle = .none
        formatter.timeStyle = .short
        return formatter.string(from: Date(timeIntervalSince1970: TimeInterval(start * 60)),
                                to: Date(timeIntervalSince1970: TimeInterval(end * 60)))
    }

    static func time(_ date: Date, locale: Locale = .current, timeZone: TimeZone = .current) -> String {
        SummaryCard.time(date, locale: locale, timeZone: timeZone)
    }
}
