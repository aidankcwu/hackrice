// The log's rows: today's decisions and the episodes no decision covered, newest first.
// Pure; Home's Log and the session detail (D-006) draw the same rows, then
// LedgerCoalescer merges repeats (D-008).
import Foundation

enum Ledger {
    static func entries(decisions: [Decision], episodes: [Episode]) -> [LedgerEntry] {
        var result: [LedgerEntry] = []
        var usedEpisodeIDs = Set<String>()

        for decision in decisions {
            let episode = decision.episodeId.flatMap { id in episodes.first { $0.id == id } }
            if let id = decision.episodeId { usedEpisodeIDs.insert(id) }
            let chip = outcome(for: decision)
            // A no-op decision (only annotate/log_insight/watch/nothing/remember, no
            // episode to anchor it) is noise, not a moment worth a row — drop it. Keep
            // every row that has an episode behind it or a chip to show.
            guard episode != nil || chip != nil else { continue }
            result.append(LedgerEntry(
                id: "decision-\(decision.id)", date: Date(timeIntervalSince1970: decision.t),
                label: label(for: decision, episode: episode), kind: episode?.kind ?? decision.trigger,
                outcome: chip, decision: decision, reported: episode?.reported))
        }
        for episode in episodes where !usedEpisodeIDs.contains(episode.id) {
            result.append(LedgerEntry(
                id: "episode-\(episode.id)", date: Date(timeIntervalSince1970: episode.startT),
                label: episode.label ?? cleanedTrigger(episode.kind), kind: episode.kind, outcome: nil,
                decision: nil, reported: episode.reported))
        }
        return result.sorted { $0.date > $1.date }
    }

    /// Footer count — the same rule as the row chips, not AppState's own tally, so the
    /// number on screen always matches what "held back" rows are actually visible above.
    static func heldBackCount(_ decisions: [Decision]) -> Int {
        decisions.filter { outcome(for: $0) == .heldBack }.count
    }

    /// Human row label: the episode's own label first, then the decision's plain-English
    /// interpretation, and only as a last resort a cleaned-up trigger. Raw triggers such
    /// as "watch:check for more caffeine" must never reach the row directly (brian-ui law).
    static func label(for decision: Decision, episode: Episode?) -> String {
        if let label = episode?.label, !label.isEmpty { return label }
        if !decision.interpretation.isEmpty { return decision.interpretation }
        return cleanedTrigger(decision.trigger)
    }

    /// Strips the internal "watch:" / "autopilot:" scheduling prefixes and turns
    /// snake_case into words, so a raw trigger never leaks as a colon-prefixed key.
    static func cleanedTrigger(_ trigger: String) -> String {
        var t = trigger
        for prefix in ["watch:", "autopilot:"] where t.hasPrefix(prefix) {
            t.removeFirst(prefix.count)
        }
        return t.replacingOccurrences(of: "_", with: " ")
    }

    /// Outcome badge, or nil for no chip at all.
    ///
    /// - `said`: spoke == true, or a proposed ask/speak that was handed off to a
    ///   conversation.
    /// - `asked`: an `ask` action still pending (not handed off) — e.g. outcome
    ///   `conversation_active`.
    /// - `acted`: a real `act` action (a calendar block, a screen shield, ...).
    /// - `held back` (brian-ui law 4): restraint, not silence — an ask or speak action
    ///   that was proposed and then suppressed (`Decision.heldBack`), and isn't already
    ///   covered by `asked` above.
    /// - nil: the decision's actions are only bookkeeping (annotate/log_insight/watch/
    ///   nothing/remember) — nothing was proposed, so there is nothing to show restraint
    ///   about.
    static func outcome(for decision: Decision) -> LedgerEntry.Outcome? {
        if decision.spoke { return .said }
        if decision.actions.contains(where: { $0.proposesSpeech && $0.handedOff }) { return .said }
        if decision.actions.contains(where: { $0.type == "ask" }) { return .asked }
        if decision.actions.contains(where: { $0.type == "act" }) { return .acted }
        if decision.heldBack { return .heldBack }
        return nil
    }
}
