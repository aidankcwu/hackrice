// APP_PRD.md "Status pill": one capsule at the top of every tab answers "is it working?".
// Tapping it opens Connect. Replaces the three-row StatusStrip that used to sit on Today.
import SwiftUI

struct StatusPill: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        // "Watching · 12 min" is time, so the pill re-reads the status once a second.
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let status = appState.connectionStatus(now: context.date)
            Button {
                appState.requestConnect()
            } label: {
                HStack(spacing: 8) {
                    Circle()
                        .fill(status.level.color)
                        .frame(width: 8, height: 8)
                    Text(status.text)
                        .font(BrianType.secondary.monospacedDigit())
                        .foregroundStyle(Brian.text)
                        .lineLimit(1)
                }
                .padding(.horizontal, 8)
                .frame(minHeight: 44)
                .contentShape(Capsule())
            }
            .accessibilityLabel(status.text)
            .accessibilityHint("Opens Connect")
        }
    }
}

/// The pill sits in the navigation bar's leading slot on every tab, opposite the Settings
/// gear. The bar's own Liquid Glass capsule is its background (never glass on glass), and
/// a large title below it stays sharp.
struct StatusPillToolbar: ToolbarContent {
    var body: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            StatusPill()
        }
    }
}
