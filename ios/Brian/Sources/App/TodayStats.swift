// DEMO_UI_PRD.md "Home · 1. Metrics": the Daylight and Screens tiles, the watched line,
// and the words in the "How this is measured" sheet. Pure; views read the results.
// D-007 adds the stats sheet (`TodayStats.derive`) here.
import Foundation

enum TodayStats {
    /// Minutes of today's episodes of one kind (`outdoor_block`, `screen_block`). Nil when
    /// no episode of that kind was seen, so the tile can say "—" instead of a misleading "0 min".
    static func minutes(kind: String, episodes: [Episode], now: Date) -> Int? {
        let matching = episodes.filter { $0.kind == kind }
        guard !matching.isEmpty else { return nil }
        let seconds = matching.reduce(0.0) { $0 + length($1, now: now) }
        return Int((seconds / 60).rounded())
    }

    /// Closed: end − start. Open: the server's `duration_s` as of the fetch, else up to `now`
    /// (an open episode from a stale fetch must not run on for days).
    static func length(_ episode: Episode, now: Date) -> TimeInterval {
        if let end = episode.endT { return max(0, end - episode.startT) }
        if let running = episode.durationS { return max(0, running) }
        return max(0, now.timeIntervalSince1970 - episode.startT)
    }

    /// "14 min", "3 h 10 min", "2 h 05 min", "2 h".
    static func durationText(minutes: Int) -> String {
        guard minutes >= 60 else { return "\(minutes) min" }
        let h = minutes / 60, m = minutes % 60
        return m == 0 ? "\(h) h" : "\(h) h " + String(format: "%02d", m) + " min"
    }
}

/// What the metrics row under the hero shows.
struct HomeMetrics: Equatable {
    /// "12 min", or nil when nothing outdoors was seen today.
    let daylight: String?
    /// "34 min", or nil when no sustained screen was seen today.
    let screens: String?
    /// "Watched 3 h 10 min today" or "Not watched yet today".
    let watchedLine: String

    static let daylightKind = "outdoor_block"
    static let screensKind = "screen_block"

    /// `dayStart` is the local midnight the day starts at; nil means today's (`now`'s).
    /// Demo mode passes the fixture day's, so its sessions count as today's.
    static func derive(episodes: [Episode], sessions: [WatchSession], watchingSince: Date?,
                       now: Date, calendar: Calendar = .current, dayStart: Date? = nil) -> HomeMetrics {
        HomeMetrics(
            daylight: TodayStats.minutes(kind: daylightKind, episodes: episodes, now: now).map(TodayStats.durationText),
            screens: TodayStats.minutes(kind: screensKind, episodes: episodes, now: now).map(TodayStats.durationText),
            watchedLine: watchedLine(seconds: watchedSeconds(sessions: sessions, watchingSince: watchingSince,
                                                             now: now, calendar: calendar, dayStart: dayStart)))
    }

    /// Seconds watched since local midnight: today's sessions when the backend has any,
    /// else the current watching span. Open spans run to `now`; spans that began
    /// yesterday count from midnight. The same window as the Sessions row's total.
    static func watchedSeconds(sessions: [WatchSession], watchingSince: Date?, now: Date,
                               calendar: Calendar = .current, dayStart: Date? = nil) -> TimeInterval {
        let start = dayStart ?? calendar.startOfDay(for: now)
        let midnight = start.timeIntervalSince1970
        let dayEnd = (calendar.date(byAdding: .day, value: 1, to: start) ?? start.addingTimeInterval(86_400))
        let end = min(now, dayEnd).timeIntervalSince1970
        func overlap(_ start: Double, _ stop: Double?) -> Double {
            max(0, min(stop ?? end, end) - max(start, midnight))
        }
        let today = sessions.map { overlap($0.startedT, $0.endedT) }.filter { $0 > 0 }
        if !today.isEmpty { return today.reduce(0, +) }
        return watchingSince.map { overlap($0.timeIntervalSince1970, nil) } ?? 0
    }

    static func watchedLine(seconds: TimeInterval) -> String {
        guard seconds > 0 else { return "Not watched yet today" }
        let minutes = Int(seconds / 60)
        guard minutes > 0 else { return "Watched under a minute today" }
        return "Watched \(TodayStats.durationText(minutes: minutes)) today"
    }
}

/// The words one factor row of the "How this is measured" sheet shows.
enum HealthFactorText {
    /// Glasses when the camera measured it (directly or derived from what it saw), Missing
    /// when nothing measured it, Seeded for everything the demo seed or a phone row filled.
    static func provenanceWord(_ factor: HealthFactor) -> String {
        let source = factor.provenance?.lowercased()
        if source == "missing" || factor.measured == false || factor.dose == nil { return "Missing" }
        if (source == "live" || source == "derived"), factor.basis?.lowercased() == "glasses" { return "Glasses" }
        return "Seeded"
    }

    /// "5,500 steps", "1.4 min", "78th percentile", "1 drink", "Not measured".
    static func dose(_ factor: HealthFactor) -> String {
        guard let dose = factor.dose, provenanceWord(factor) != "Missing" else { return "Not measured" }
        switch factor.key {
        case "smoker":
            return dose >= 0.5 ? "Yes" : "No"
        case "fitness_pct":
            let ordinal = NumberFormatter()
            ordinal.numberStyle = .ordinal
            ordinal.locale = Locale(identifier: "en_US")
            return (ordinal.string(from: NSNumber(value: Int(dose.rounded()))) ?? number(dose)) + " percentile"
        default:
            break
        }
        let text = number(dose)
        guard let unit = unit(for: factor.key, dose: dose) ?? factor.unit else { return text }
        return unit.hasPrefix("×") ? "\(text)\(unit)" : "\(text) \(unit)"
    }

    /// Whole numbers from 10 up ("5,500", "94"), one decimal below ("1.4", "0").
    static func number(_ value: Double) -> String {
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.numberStyle = .decimal
        formatter.minimumFractionDigits = 0
        formatter.maximumFractionDigits = abs(value) >= 10 ? 0 : 1
        return formatter.string(from: NSNumber(value: value)) ?? String(value)
    }

    /// Plain-word units for the backend's factor keys (brian_score.py FACTORS).
    static func unit(for key: String, dose: Double) -> String? {
        let one = number(dose) == "1"
        switch key {
        case "steps": return one ? "step" : "steps"
        case "vilpa_min", "day_light_min": return "min"
        case "resistance_min_wk", "nature_min_wk": return "min this week"
        case "gait_speed": return "m/s"
        case "sleep_hours": return "h"
        case "sri", "social_index": return "of 100"
        case "night_light_lux": return "lx"
        case "purpose": return "of 6"
        case "pm25": return "µg/m³"
        case "noise_night_db": return "dB"
        case "med_adherence": return "of 1"
        case "alcohol_drinks": return one ? "drink" : "drinks"
        case "sauna_wk": return one ? "session this week" : "sessions this week"
        case "rt_z": return "z"
        case "recovery_ratio": return "× baseline"
        default: return nil
        }
    }

    /// The study, without the finding after its colon: "Paluch 2022 Lancet Public Health
    /// (15 cohorts, n=47,471)".
    static func citation(_ factor: HealthFactor) -> String? {
        guard let source = factor.source?.trimmingCharacters(in: .whitespaces), !source.isEmpty else { return nil }
        guard let colon = source.range(of: ": ") else { return source }
        return String(source[..<colon.lowerBound])
    }

    /// Biggest effect first; unmeasured factors last. Ties keep the backend's order.
    static func ordered(_ factors: [HealthFactor]) -> [HealthFactor] {
        factors.enumerated().sorted { a, b in
            let aMissing = provenanceWord(a.element) == "Missing"
            let bMissing = provenanceWord(b.element) == "Missing"
            if aMissing != bMissing { return !aMissing }
            let aSize = abs(Hours.rounded(a.element.hours)), bSize = abs(Hours.rounded(b.element.hours))
            if aSize != bSize { return aSize > bSize }
            return a.offset < b.offset
        }.map(\.element)
    }
}
