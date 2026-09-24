import SwiftUI
import UIKit

struct ProtocolView: View {
    @Environment(AppState.self) private var appState
    @State private var showAddItem = false

    var body: some View {
        List {
            if appState.protocolItems.isEmpty {
                Text("No items yet. Add the first dose window.")
                    .foregroundStyle(Brian.muted)
            } else {
                ForEach(appState.protocolItems) { item in
                    protocolRow(item)
                        .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                            Button(role: .destructive) {
                                Task { await appState.deleteProtocolItem(item) }
                            } label: { Label("Delete", systemImage: "trash") }
                        }
                        .swipeActions(edge: .leading, allowsFullSwipe: true) {
                            if item.status == "seen" || item.status == "done" {
                                Button("Undo") { Task { await appState.undo(item) } }
                                    .tint(Brian.muted)
                            } else {
                                Button("Mark done") { Task { await appState.markDone(item) } }
                                    .tint(Brian.ink)
                            }
                        }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Protocol")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showAddItem = true } label: { Image(systemName: "plus") }
                    .buttonStyle(.glass)
                    .accessibilityLabel("Add item")
            }
        }
        .refreshable { await appState.refreshProtocol() }
        .task { await appState.refreshProtocol() }
        .sheet(isPresented: $showAddItem) { AddItemView() }
    }

    private func protocolRow(_ item: ProtocolItem) -> some View {
        HStack(spacing: 12) {
            Image(systemName: BrianSymbol.protocolKind(item.kind.lowercased()))
                .frame(width: 24)
                .foregroundStyle(Brian.muted)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(item.name)
                Text("\(formatTime(item.windowStart))–\(formatTime(item.windowEnd))")
                    .font(BrianType.secondary)
                    .foregroundStyle(Brian.muted)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
            Spacer()
            if item.status == "seen" && item.evidenceRef != nil {
                ProtocolEvidenceThumbnail(item: item)
            }
            Text(status(for: item))
                .font(BrianType.secondary)
                .foregroundStyle(Brian.muted)
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
        }
        .frame(minHeight: 52)
        .accessibilityElement(children: .combine)
    }

    private func status(for item: ProtocolItem) -> String {
        switch item.status.lowercased() {
        case "seen":
            if let seen = item.seenAt { return "Seen \(Date(timeIntervalSince1970: seen).formatted(date: .omitted, time: .shortened))" }
            return "Seen"
        case "missed": return "Missed"
        case "done": return "Marked done"
        default: return "Waiting"
        }
    }

    private func formatTime(_ value: String) -> String {
        let parts = value.split(separator: ":")
        guard parts.count >= 2, let hour = Int(parts[0]), let minute = Int(parts[1]) else { return value }
        var components = DateComponents()
        components.hour = hour
        components.minute = minute
        return Calendar.current.date(from: components)?.formatted(date: .omitted, time: .shortened) ?? value
    }
}

private struct ProtocolEvidenceThumbnail: View {
    @Environment(AppState.self) private var appState
    let item: ProtocolItem
    @State private var data: Data?

    var body: some View {
        Group {
            if let data, let image = UIImage(data: data) {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                Image(systemName: "photo").foregroundStyle(Brian.muted)
            }
        }
        .frame(width: 44, height: 44)
        .background(Brian.surface2)
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .accessibilityLabel("Evidence thumbnail")
        .task(id: item.id) { data = await appState.evidenceThumbnail(for: item) }
    }
}
