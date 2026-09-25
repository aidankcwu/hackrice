// DEMO_UI_PRD.md "Home · 6. Log": neighbouring rows with the same label within 10 minutes
// are one moment on the log ("outdoors" twice, nine seconds apart, is one walk outside).
// Pure; Home's Log and the session detail both run their rows through it.
import Foundation

enum LedgerCoalescer {
    static let window: TimeInterval = 10 * 60

    /// `entries` newest first, as `Ledger.entries` returns them. A row merges into the one
    /// above it when the labels match (case and spaces aside) and it is at most `window`
    /// older than that group's earliest row so far, so a steady stream chains. The merged
    /// row takes the earliest row's time and id, and the strongest outcome with its
    /// decision (said > asked > acted > held back).
    static func coalesce(_ entries: [LedgerEntry], window: TimeInterval = window) -> [LedgerEntry] {
        var groups: [[LedgerEntry]] = []
        for entry in entries {
            if let earliest = groups.last?.last,
               key(earliest.label) == key(entry.label),
               earliest.date.timeIntervalSince(entry.date) <= window {
                groups[groups.count - 1].append(entry)
            } else {
                groups.append([entry])
            }
        }
        return groups.map(merge)
    }

    /// Higher is stronger; rows without an outcome are weakest.
    static func strength(_ outcome: LedgerEntry.Outcome?) -> Int {
        switch outcome {
        case .said: 4
        case .asked: 3
        case .acted: 2
        case .heldBack: 1
        case nil: 0
        }
    }

    private static func key(_ label: String) -> String {
        label.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    private static func merge(_ group: [LedgerEntry]) -> LedgerEntry {
        guard group.count > 1, let earliest = group.last else { return group[0] }
        // The row to open: the strongest outcome's, else any row that has a decision behind it.
        let strongest = group.max { strength($0.outcome) < strength($1.outcome) }!
        let lead = strongest.outcome != nil ? strongest : (group.first { $0.decision != nil } ?? earliest)
        return LedgerEntry(id: earliest.id, date: earliest.date, label: earliest.label, kind: lead.kind,
                           outcome: lead.outcome, decision: lead.decision, reported: lead.reported)
    }
}
