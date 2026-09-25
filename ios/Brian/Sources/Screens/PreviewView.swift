// DEMO_UI_PRD.md "Preview sheet". The header's Preview button opens it. The live frame
// arrives in D-002; until then the sheet says no frame has reached it.
import SwiftUI

struct PreviewView: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                Text("No frames yet")
                    .font(BrianType.secondary)
                    .foregroundStyle(Brian.muted)
                Spacer()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Space.gutter)
            .background(Brian.page)
            .navigationTitle("Glasses view")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
