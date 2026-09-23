import SwiftUI

struct LedgerEntry: Identifiable {
    enum Outcome: String {
        case heldBack = "held back"
        case said
        case asked
        case acted

        var symbol: String? {
            switch self {
            case .heldBack: nil
            case .said: "waveform"
            case .asked: "questionmark.bubble"
            case .acted: "checkmark.seal.fill"
            }
        }
    }

    let id: String
    let date: Date
    let label: String
    let kind: String
    /// nil when the decision proposed nothing (only annotate/log_insight/watch/nothing/
    /// remember) — those rows carry no chip at all, not even "held back". Episode-only
    /// rows (no decision) are also always nil.
    let outcome: Outcome?
    let decision: Decision?
    let reported: Reported?
}

struct LedgerRow: View {
    let entry: LedgerEntry

    var body: some View {
        HStack(spacing: 8) {
            Text(entry.date, format: .dateTime.hour().minute())
                .font(BrianType.secondary.monospacedDigit())
                .foregroundStyle(Brian.muted)
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
                .frame(minWidth: Space.timeColumn, alignment: .leading)

            Image(systemName: BrianSymbol.family(entry.kind))
                .font(.system(size: 18))
                .foregroundStyle(Brian.muted)
                .frame(width: 18)
                .accessibilityHidden(true)

            Text(entry.label)
                .font(BrianType.body)
                .foregroundStyle(entry.outcome == .heldBack ? Brian.muted : Brian.text)
                .frame(maxWidth: .infinity, alignment: .leading)

            if let outcome = entry.outcome {
                HStack(spacing: 4) {
                    if let symbol = outcome.symbol {
                        Image(systemName: symbol).accessibilityHidden(true)
                    }
                    Text(outcome.rawValue)
                }
                .font(BrianType.outcome)
                .foregroundStyle(Brian.muted)
            }
        }
        .frame(minHeight: Space.logRow)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
