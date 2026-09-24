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
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // At accessibility sizes the time and the outcome take their own lines, so the title
        // keeps the width instead of wrapping a word per line.
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4))
            : AnyLayout(HStackLayout(spacing: 8))
        layout {
            Text(entry.date, format: .dateTime.hour().minute())
                .font(BrianType.secondary.monospacedDigit())
                .foregroundStyle(Brian.muted)
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
                .frame(minWidth: Space.timeColumn, alignment: .leading)

            HStack(spacing: 8) {
                Image(systemName: BrianSymbol.family(entry.kind))
                    .font(.system(size: 18))
                    .foregroundStyle(Brian.muted)
                    .frame(width: 18)
                    .padding(.trailing, 8)   // symbol 18 · 16 · title, per the design skill
                    .accessibilityHidden(true)

                // Episode labels arrive lower case ("meal, rice bowl"); the ledger is sentence case.
                Text(entry.label.prefix(1).uppercased() + entry.label.dropFirst())
                    .font(BrianType.body)
                    .foregroundStyle(entry.outcome == .heldBack ? Brian.muted : Brian.text)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

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
