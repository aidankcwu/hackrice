import SwiftUI

/// A protocol item's state: "Seen 8:42 AM" and "Done" in ink with a checkmark, "Missed" in
/// `Brian.cost` with a cross, "Open until 10 PM" and "Later" as muted words.
struct ProtocolStateLabel: View {
    let row: ProtocolSummary.Row
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // One line beside a name; at accessibility sizes it sits under the name and may wrap
        // ("Seen" / "8:42 AM") instead of running into the right gutter.
        let wraps = typeSize.isAccessibilitySize
        HStack(alignment: .firstTextBaseline, spacing: 4) {
            if let symbol = row.stateSymbol {
                Image(systemName: symbol).accessibilityHidden(true)
            }
            Text(row.state).monospacedDigit()
        }
        .font(BrianType.secondary)
        .foregroundStyle(color)
        .lineLimit(wraps ? nil : 1)
        .fixedSize(horizontal: !wraps, vertical: false)
    }

    private var color: Color {
        switch row.tone {
        case .complete: Brian.ink
        case .missed: Brian.cost
        case .pending: Brian.muted
        }
    }
}
