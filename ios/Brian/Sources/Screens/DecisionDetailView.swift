import SwiftUI
import UIKit

struct DecisionDetailView: View {
    @Environment(AppState.self) private var appState
    let decision: Decision
    let reported: Reported?
    @State private var thumbnailData: Data?

    var body: some View {
        List {
            Section("Interpretation") {
                Text(decision.interpretation)
            }

            if !decision.actions.isEmpty {
                Section("Actions taken") {
                    ForEach(Array(decision.actions.enumerated()), id: \.offset) { _, action in
                        Text(action.text ?? action.line ?? action.type)
                    }
                }
            }

            if let thumbnailData, let image = UIImage(data: thumbnailData) {
                Section("Evidence") {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .accessibilityLabel("Evidence thumbnail")
                }
            }

            if reported != nil {
                Section {
                    Label("Wearer reported", systemImage: "person.crop.circle.badge.checkmark")
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Decision")
        .navigationBarTitleDisplayMode(.inline)
        .task { thumbnailData = await appState.evidenceThumbnail(for: decision) }
    }
}
