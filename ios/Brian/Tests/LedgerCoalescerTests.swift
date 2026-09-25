// DEMO_UI_PRD.md "Home · 6. Log": same label within 10 minutes is one row, earliest time,
// strongest outcome (said > asked > acted > held back).
import Foundation
import Testing
@testable import Brian

@MainActor
struct LedgerCoalescerTests {
    private let t0: Double = 1_790_110_000

    private func decision(_ id: String, _ outcome: LedgerEntry.Outcome?) -> Decision {
        let actions: [DecisionAction] = switch outcome {
        case .said: [DecisionAction(type: "speak")]
        case .asked: [DecisionAction(type: "ask")]
        case .acted: [DecisionAction(type: "act")]
        case .heldBack, nil: [DecisionAction(type: "annotate")]
        }
        return Decision(id: id, t: t0, trigger: "t", actions: actions, spoke: outcome == .said)
    }

    private func entry(_ id: String, _ label: String, minute: Double, _ outcome: LedgerEntry.Outcome? = nil,
                       withDecision: Bool = false) -> LedgerEntry {
        LedgerEntry(id: id, date: Date(timeIntervalSince1970: t0 + minute * 60), label: label, kind: label,
                    outcome: outcome,
                    decision: (outcome != nil || withDecision) ? decision("d-\(id)", outcome) : nil,
                    reported: nil)
    }

    @Test func sameLabelWithinTenMinutesIsOneRowAtTheEarliestTime() {
        let rows = LedgerCoalescer.coalesce([
            entry("b", "outdoors", minute: 0.15),
            entry("a", "outdoors", minute: 0),
        ])
        #expect(rows.map(\.id) == ["a"])
        #expect(rows[0].date == Date(timeIntervalSince1970: t0))
    }

    @Test func moreThanTenMinutesApartStaysTwoRows() {
        let rows = LedgerCoalescer.coalesce([
            entry("b", "outdoors", minute: 10.5),
            entry("a", "outdoors", minute: 0),
        ])
        #expect(rows.map(\.id) == ["b", "a"])
        // Exactly ten minutes still merges.
        #expect(LedgerCoalescer.coalesce([entry("b", "outdoors", minute: 10), entry("a", "outdoors", minute: 0)])
            .map(\.id) == ["a"])
    }

    @Test func aSteadyStreamChains() {
        let rows = LedgerCoalescer.coalesce([
            entry("c", "screen", minute: 16),
            entry("b", "screen", minute: 8),
            entry("a", "screen", minute: 0),
        ])
        #expect(rows.map(\.id) == ["a"])
    }

    @Test func onlyNeighboursMerge() {
        let rows = LedgerCoalescer.coalesce([
            entry("c", "outdoors", minute: 2),
            entry("b", "meal", minute: 1),
            entry("a", "outdoors", minute: 0),
        ])
        #expect(rows.map(\.label) == ["outdoors", "meal", "outdoors"])
    }

    @Test func labelsMatchIgnoringCaseAndSpaces() {
        let rows = LedgerCoalescer.coalesce([
            entry("b", "Outdoors ", minute: 1),
            entry("a", "outdoors", minute: 0),
        ])
        #expect(rows.count == 1)
        #expect(rows[0].label == "outdoors")
    }

    @Test func theStrongestOutcomeWinsWithItsDecision() {
        let order: [LedgerEntry.Outcome] = [.said, .asked, .acted, .heldBack]
        for (i, strong) in order.enumerated() {
            for weak in order[(i + 1)...] {
                // Either way round: strongest kept whether it is the newer or the older row.
                for pair in [[entry("n", "x", minute: 1, strong), entry("o", "x", minute: 0, weak)],
                             [entry("n", "x", minute: 1, weak), entry("o", "x", minute: 0, strong)]] {
                    let merged = LedgerCoalescer.coalesce(pair)
                    #expect(merged.count == 1)
                    #expect(merged[0].outcome == strong)
                    #expect(merged[0].id == "o")
                    let owner = pair.first { $0.outcome == strong }!
                    #expect(merged[0].decision?.id == owner.decision?.id)
                }
            }
        }
    }

    @Test func noOutcomeKeepsARowThatOpensADecision() {
        let merged = LedgerCoalescer.coalesce([
            entry("b", "x", minute: 1),
            entry("a", "x", minute: 0, withDecision: true),
        ])
        #expect(merged.count == 1)
        #expect(merged[0].outcome == nil)
        #expect(merged[0].decision?.id == "d-a")
    }

    @Test func emptyAndSingleRowsPassThrough() {
        #expect(LedgerCoalescer.coalesce([]).isEmpty)
        let one = entry("a", "meal", minute: 0, .said)
        let rows = LedgerCoalescer.coalesce([one])
        #expect(rows.map(\.id) == ["a"])
        #expect(rows[0].outcome == .said)
    }

    // MARK: Demo fixture

    @Test func theFixtureHasNoAdjacentDuplicateLabels() async {
        let state = AppState(demo: true)
        await state.refreshToday()
        let raw = Ledger.entries(decisions: state.decisions, episodes: state.episodes)
        let rows = LedgerCoalescer.coalesce(raw)
        // The fixture's two "outdoors" rows nine seconds apart (e_1797, e_1798) become one.
        #expect(raw.prefix(2).map(\.label) == ["outdoors", "outdoors"])
        #expect(rows.count < raw.count)
        #expect(rows.first?.label == "outdoors")
        #expect(rows.first?.id == "decision-d_3156")
        for (newer, older) in zip(rows, rows.dropFirst()) {
            #expect(newer.label.lowercased() != older.label.lowercased())
        }
        #expect(zip(rows, rows.dropFirst()).allSatisfy { $0.date >= $1.date })
    }
}
