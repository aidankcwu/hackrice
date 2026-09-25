// DEMO_UI_PRD.md "Preview sheet" (D-002). The header's Preview button opens it: the most
// recent camera frame, refreshed at most twice a second, and one line under it. Frames
// reach AppState only while this sheet is on screen (openPreview / closePreview), and the
// last one is dropped when it closes. Nothing is written to disk.
import SwiftUI

struct PreviewView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let frame = appState.previewFrame {
                        // Under the picture as the PRD draws it; above it at accessibility
                        // sizes, where the picture alone fills the screen.
                        if dynamicTypeSize.isAccessibilitySize { liveLine }
                        Image(uiImage: frame)
                            .resizable()
                            .scaledToFit()
                            .clipShape(RoundedRectangle(cornerRadius: Brian.panelRadius, style: .continuous))
                            .frame(maxWidth: .infinity)
                            .accessibilityLabel("Latest frame from the glasses")
                        if !dynamicTypeSize.isAccessibilitySize { liveLine }
                    } else {
                        Text("No frames yet")
                            .font(BrianType.body)
                            .foregroundStyle(Brian.ink)
                        Text(appState.watching
                             ? "The first frame shows here a few seconds after the camera starts."
                             : "Tap Start watching and the camera's view shows here.")
                            .font(BrianType.secondary)
                            .foregroundStyle(Brian.muted)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(Space.gutter)
            }
            .background(Brian.page)
            .navigationTitle("Glasses view")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .onAppear { appState.openPreview() }
        .onDisappear { appState.closePreview() }
    }

    /// "Live · 0.7 frames/s" is a rate over time, so it re-reads once a second.
    private var liveLine: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            Text(appState.preview.line(now: context.date))
                .font(BrianType.secondary.monospacedDigit())
                .foregroundStyle(Brian.muted)
        }
    }
}
