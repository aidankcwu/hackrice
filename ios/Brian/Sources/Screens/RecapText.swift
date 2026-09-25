// A recap as the backend wrote it (never rewritten on the phone): the headline, the
// paragraphs, then the suggestions. Home's daily summary and the session detail share it.
import SwiftUI

struct RecapText: View {
    let recap: Recap

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(recap.headline)
                .font(BrianType.body.weight(.semibold))
                .foregroundStyle(Brian.ink)
            ForEach(Array(recap.paragraphs.enumerated()), id: \.offset) { _, paragraph in
                Text(paragraph)
                    .font(BrianType.body)
                    .foregroundStyle(Brian.text)
            }
            if !recap.suggestions.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(Array(recap.suggestions.enumerated()), id: \.offset) { _, suggestion in
                        Label {
                            Text(suggestion).foregroundStyle(Brian.text)
                        } icon: {
                            Image(systemName: "arrow.turn.down.right").foregroundStyle(Brian.muted)
                        }
                        .font(BrianType.body)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
