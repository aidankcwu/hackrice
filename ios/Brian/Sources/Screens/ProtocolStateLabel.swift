import SwiftUI

/// A protocol item's state: "Seen 8:42 AM" and "Done" in ink with a checkmark, "Missed" in
/// `Brian.cost` with a cross, "Open until 10 PM" and "Later" as muted words.
struct ProtocolStateLabel: View {
    let row: ProtocolSummary.Row

    var body: some View {
        HStack(spacing: 4) {
            if let symbol = row.stateSymbol {
                Image(systemName: symbol).accessibilityHidden(true)
            }
            Text(row.state).monospacedDigit()
        }
        .font(BrianType.secondary)
        .foregroundStyle(color)
        .lineLimit(1)
        .fixedSize(horizontal: true, vertical: false)
    }

    private var color: Color {
        switch row.tone {
        case .complete: Brian.ink
        case .missed: Brian.cost
        case .pending: Brian.muted
        }
    }
}
