// DEMO_UI_PRD.md "Home · 1. Metrics": the sheet behind the hero. One plain paragraph,
// then every factor behind today's hours: label, dose, signed hours, where the dose came
// from, and the study it rests on.
import SwiftUI

struct MeasuredView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var typeSize

    static let explanation = "Each thing the glasses saw today is matched to a published dose-response study and turned into hours of healthy life gained or lost. Anything not seen counts as average, never as a gain."

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text(Self.explanation)
                        .font(BrianType.body)
                        .foregroundStyle(Brian.text)
                        .padding(.vertical, 4)
                }

                Section("Today") {
                    if factors.isEmpty {
                        Text("Nothing measured yet today. Put the glasses on and the factors fill in.")
                            .font(BrianType.body)
                            .foregroundStyle(Brian.muted)
                    } else {
                        ForEach(factors) { factor in
                            row(factor)
                        }
                    }
                }
                .textCase(nil)
            }
            .listStyle(.insetGrouped)
            .navigationTitle("How this is measured")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private var factors: [HealthFactor] {
        HealthFactorText.ordered(appState.healthspan?.factors ?? [])
    }

    private func row(_ factor: HealthFactor) -> some View {
        let top = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4))
            : AnyLayout(HStackLayout(alignment: .firstTextBaseline, spacing: 8))
        return VStack(alignment: .leading, spacing: 4) {
            top {
                Text(factor.label)
                    .font(BrianType.body)
                    .foregroundStyle(Brian.text)
                    .frame(maxWidth: .infinity, alignment: .leading)
                SignedHoursChip(hours: factor.hours)
            }
            Text("\(HealthFactorText.dose(factor)) · \(HealthFactorText.provenanceWord(factor))")
                .font(BrianType.secondary)
                .foregroundStyle(Brian.muted)
            if let citation = HealthFactorText.citation(factor) {
                Text(citation)
                    .font(BrianType.caption)
                    .foregroundStyle(Brian.muted)
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}
